"""
run_and_verify.py — driver that runs every enabled supplier, uploads products,
and VERIFIES each newly-created product on the live site.

Requirements implemented (per user request):
  * Run all enabled suppliers.
  * A product that already exists on the site is only UPDATED (stock in/out +
    price), never re-uploaded.
  * Each newly-created product is verified on the site for: image, price,
    description (פירוט), tags (תיוגים), category (קטגוריה).
  * If a single product fails verification/upload after MAX_ATTEMPTS (5) tries,
    abort the rest of that supplier and move on to the next supplier.
  * A full JSON report is written to data/reports/verify_<runid>.json
"""
import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

from src.core.config_loader import (
    load_app_settings,
    load_suppliers,
    load_category_mapping,
    load_keyword_rules,
    load_shipping_class_mapping,
)
from src.core.constants import STATUS_DRAFT, STATUS_PUBLISH, STOCK_IN, STOCK_OUT
from src.core.exceptions import WooCommerceError
from src.core.logger import get_logger
from src.enrichment.openai_client import OpenAIClient
from src.enrichment.product_content_generator import ProductContentGenerator
from src.matching.category_matcher import CategoryMatcher
from src.matching.product_matcher import ProductMatcher
from src.woocommerce.client import WooCommerceClient
from src.woocommerce.category_service import CategoryService
from src.woocommerce.media_service import MediaService
from src.woocommerce.product_service import ProductService

# Reuse the exact supplier factory + image-payload builder from main
from src.main import build_supplier, _build_images_payload

logger = get_logger("run_and_verify")

MAX_ATTEMPTS = 5
DEFAULT_FALLBACK_CAT = "מוצרים נוספים"

import re

_DUP_RESOURCE_RE = re.compile(r'"resource_id"\s*:\s*(\d+)')


def _is_duplicate_sku_error(exc) -> bool:
    s = str(exc)
    return ("product_invalid_sku" in s) or ("כפול" in s) or ("already" in s.lower() and "sku" in s.lower())


def _is_image_error(exc) -> bool:
    s = str(exc)
    return ("product_image_upload_error" in s) or ("תמונה לא תקפה" in s) or ("image" in s.lower() and "upload" in s.lower())


def _is_server_5xx(exc) -> bool:
    """Server-side error (500/502/503/504), often a timeout while WC sideloads
    a slow/large supplier image during create."""
    s = str(exc)
    return ("too many 500 error" in s or "too many 502 error" in s
            or "too many 503 error" in s or "too many 504 error" in s
            or "-> 500:" in s or "-> 502:" in s or "-> 503:" in s or "-> 504:" in s)


def _resource_id_from_error(exc):
    m = _DUP_RESOURCE_RE.search(str(exc))
    return int(m.group(1)) if m else None


def fix_existing_fields(product, existing, config, product_svc, category_svc,
                        content_generator, category_ids):
    """
    For an existing product that is MISSING required fields, push the missing
    ones only (tags / proper category) — never touches image, name or
    description, so it is not a re-upload of the product. Returns a list of
    human-readable change strings.
    """
    if category_svc is None:
        return []
    wc_id = existing["id"]
    payload = {}
    changes = []

    needs_tags = not (existing.get("tags") or [])
    cat_names = [c.get("name", "") for c in (existing.get("categories") or [])]
    needs_cat = (not cat_names) or all(n == config.default_category for n in cat_names)

    if needs_tags and content_generator is not None and content_generator.available:
        try:
            product = content_generator.enrich(product)
        except Exception as exc:
            logger.warning(f"  fixup enrich failed [{product.sku}]: {exc}")
        if product.tags:
            payload["tags"] = [{"name": t} for t in product.tags]
            changes.append(f"+{len(product.tags)} tags")

    if needs_cat and category_ids and product.mapped_category \
            and product.mapped_category != config.default_category:
        payload["categories"] = [{"id": cid} for cid in category_ids]
        changes.append(f"category→{product.mapped_category}")

    if payload:
        try:
            product_svc._client.put(f"products/{wc_id}", payload)
        except Exception as exc:
            logger.warning(f"  fixup push failed [{product.sku}]: {exc}")
            return []
    return changes


def update_existing_product(product, existing, config, product_svc, stats,
                            category_svc=None, content_generator=None, category_ids=None,
                            media_svc=None, supplier=None):
    """
    Update an already-existing WC product: stock in/out + price only, PLUS a
    targeted fix of missing tags/category/image. NEVER re-uploads name or
    description (honours the 'existing → update only, don't re-upload' rule).
    Returns ("ok_updated", detail).
    """
    wc_id = existing["id"]
    changes = []
    # ── fix missing required fields (tags / category) without re-uploading ──
    changes += fix_existing_fields(product, existing, config, product_svc,
                                   category_svc, content_generator, category_ids)

    # ── fix a MISSING image (e.g. a no-image draft created earlier when the
    #    Paldinox token had expired). Re-host the image and, if the product
    #    was only a draft for lack of an image and is in stock, publish it. ──
    if (not (existing.get("images") or [])) and product.images and config.sync_images \
            and media_svc is not None:
        ids = media_svc.upload_product_images(product.images)
        if not ids and supplier is not None and hasattr(supplier, "_login"):
            if supplier._login():
                ids = media_svc.upload_product_images(product.images)
        if ids:
            payload = {"images": [{"id": i} for i in ids]}
            if existing.get("status") == "draft" and product.is_available:
                payload["status"] = STATUS_PUBLISH
                changes.append("+image, published")
            else:
                changes.append("+image")
            try:
                product_svc._client.put(f"products/{wc_id}", payload)
            except Exception as exc:
                logger.warning(f"  image fixup push failed [{product.sku}]: {exc}")
    wc_stock = existing.get("stock_status", STOCK_IN)
    if product.stock_status != wc_stock:
        if not product.is_available and config.draft_when_out_of_stock:
            product_svc.set_out_of_stock(wc_id)
            stats["out_of_stock"] += 1
            changes.append("stock→outofstock")
        elif product.is_available and config.allow_republish:
            # NEVER republish a product with no image. A no-image product is
            # kept as a draft by create/fixup; the image-fixup block above
            # publishes it if it manages to add an image. If it's still
            # imageless here, mark it in-stock but LEAVE IT A DRAFT — otherwise
            # a returning-to-stock item goes live without a picture.
            has_image = bool(existing.get("images")) or ("+image, published" in changes)
            if has_image:
                product_svc.set_publish(wc_id)
                product_svc.update_stock_only(wc_id, STOCK_IN)
                changes.append("stock→instock")
            else:
                product_svc.update_stock_only(wc_id, STOCK_IN)
                changes.append("instock but no image → kept draft")
        else:
            product_svc.update_stock_only(wc_id, product.stock_status)
            changes.append(f"stock→{product.stock_status}")
    wc_price_str = existing.get("regular_price", "0") or "0"
    try:
        wc_price = float(wc_price_str)
    except ValueError:
        wc_price = 0.0
    if abs(product.calculated_price - wc_price) > 0.5:
        product_svc.update_price_only(wc_id, product)
        changes.append(f"price {wc_price}→{product.calculated_price}")
    if not changes:
        # No stock/price change → SKIP the WC write. A per-product "sync touch"
        # write for every unchanged item (~5000 writes/run) is what pushed the
        # weekly sync past its 5h50m timeout before it finished all suppliers.
        # Reads still happen (scrape + in-memory SKU cache), just no pointless PUT.
        changes.append("no change")
    return ("ok_updated", {"wc_id": wc_id, "sku": product.sku,
                            "name": product.name[:60], "changes": changes})


# ──────────────────────────────────────────────────────────────────
# Verification of a product as it actually appears on the live site
# ──────────────────────────────────────────────────────────────────

def verify_live_product(wc_obj: dict, fallback_cat: str = DEFAULT_FALLBACK_CAT):
    """
    Inspect a WooCommerce product object (as returned by the API) and report
    any missing required fields. Returns (ok: bool, problems: list[str],
    warnings: list[str]).
    """
    problems: list[str] = []
    warnings_: list[str] = []

    # image
    images = wc_obj.get("images") or []
    real_images = [im for im in images if (im.get("src") or im.get("id"))]
    if not real_images:
        problems.append("no image")

    # price
    raw_price = wc_obj.get("regular_price") or wc_obj.get("price") or "0"
    try:
        price = float(raw_price)
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        problems.append("no/zero price")

    # description (פירוט)
    desc = (wc_obj.get("description") or "").strip()
    short = (wc_obj.get("short_description") or "").strip()
    if len(desc) < 20 and len(short) < 20:
        problems.append("missing/short description")

    # tags (תיוגים)
    if not (wc_obj.get("tags") or []):
        problems.append("no tags")

    # category (קטגוריה)
    cats = wc_obj.get("categories") or []
    cat_names = [c.get("name", "") for c in cats]
    if not cats:
        problems.append("no category")
    elif all(n == fallback_cat for n in cat_names):
        warnings_.append(f"only fallback category ('{fallback_cat}')")

    return (len(problems) == 0, problems, warnings_)


# ──────────────────────────────────────────────────────────────────
# Process one product with retry + verify
# ──────────────────────────────────────────────────────────────────

def process_product(
    product,
    config,
    product_svc,
    media_svc,
    category_svc,
    category_matcher,
    content_generator,
    shipping_class_map,
    stats,
    supplier=None,
):
    """
    Returns one of:
      ("ok_created", detail) / ("ok_updated", detail) / ("ok_restored", detail)
      ("fail", detail)   -> product could not be uploaded cleanly after retries
    """
    # ── category matching ──
    product.mapped_category = category_matcher.match(
        supplier_key=config.key,
        supplier_category=product.supplier_category,
        product_name=product.name,
        product_description=product.original_description,
        default_category=config.default_category,
    )

    # ── out-of-stock handling ──
    if not product.is_available and config.draft_when_out_of_stock:
        product.status = STATUS_DRAFT
        product.stock_status = STOCK_OUT

    category_ids = category_svc.resolve_list(product.mapped_category, config.default_category)
    shipping_class = shipping_class_map.get(product.mapped_category, "")

    matcher = ProductMatcher(product_svc)
    existing = matcher.find_existing(product)

    # ════════════════════════════════════════════════════════════════
    # EXISTING PRODUCT  → update only (stock / price), never re-upload
    # ════════════════════════════════════════════════════════════════
    if existing and existing.get("status") != "trash":
        return update_existing_product(product, existing, config, product_svc, stats,
                                       category_svc=category_svc, content_generator=content_generator,
                                       category_ids=category_ids, media_svc=media_svc, supplier=supplier)

    # ════════════════════════════════════════════════════════════════
    # TRASHED  → restore (full re-push)
    # ════════════════════════════════════════════════════════════════
    restoring = bool(existing and existing.get("status") == "trash")
    wc_id_existing = existing["id"] if existing else None

    # ── enrich once (AI: description, tags, seo) ──
    if content_generator.available:
        try:
            product = content_generator.enrich(product)
        except Exception as exc:
            logger.warning(f"  enrich failed for {product.sku}: {exc}")

    # ════════════════════════════════════════════════════════════════
    # NEW / RESTORE  → create (or update if restoring) with retry+verify
    # ════════════════════════════════════════════════════════════════
    last_problems = []
    rehosted_ids = None     # once images are re-hosted via WP media, reuse them
    action_label = "ok_restored" if restoring else "ok_created"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        # ── build image payload for this attempt ──
        if rehosted_ids is not None:
            images_payload = [{"id": i} for i in rehosted_ids]
        elif config.key == "paldinox" and config.sync_images and product.images:
            ids = media_svc.upload_product_images(product.images)
            if not ids and supplier is not None and hasattr(supplier, "_login"):
                # Paldinox image URLs are token-protected and the token expires
                # ~1h after login; on a long run the download starts 401-ing.
                # Refresh the token and retry once.
                logger.info(f"  🔑 image download empty → refreshing Paldinox token [{product.sku}]")
                if supplier._login():
                    ids = media_svc.upload_product_images(product.images)
            images_payload = [{"id": mid} for mid in ids] if ids else []
        else:
            images_payload = _build_images_payload(product, config)

        try:
            if restoring or wc_id_existing:
                wc_obj = product_svc.update(wc_id_existing, product, category_ids,
                                            images_payload, shipping_class=shipping_class)
            else:
                wc_obj = product_svc.create(product, category_ids, images_payload,
                                            shipping_class=shipping_class)
                wc_id_existing = wc_obj.get("id")  # so retries update, not re-create

            # ── VERIFY on the live object ──
            ok, problems, warns = verify_live_product(wc_obj, fallback_cat=config.default_category)
            if ok:
                return (action_label, {
                    "wc_id": wc_obj.get("id"), "sku": product.sku,
                    "name": product.display_name()[:60], "attempt": attempt,
                    "category": [c.get("name") for c in wc_obj.get("categories", [])],
                    "rehosted": rehosted_ids is not None, "warnings": warns})

            last_problems = problems
            logger.warning(
                f"  ⚠️  attempt {attempt}/{MAX_ATTEMPTS} [{product.sku}] bad upload: "
                f"{', '.join(problems)}")
            # verify says no image → try re-host via WP media, else DRAFT it.
            # We draft even when the scraper found no image URLs at all
            # (product.images empty) — a public product must never be imageless.
            if "no image" in problems and config.sync_images:
                if product.images and rehosted_ids is None:
                    ids = media_svc.upload_product_images(product.images)
                    if ids:
                        rehosted_ids = ids
                        logger.info(f"  🖼️  re-hosted {len(ids)} images → retrying [{product.sku}]")
                        continue
                # no image obtainable (empty source or rehost failed) → draft
                if wc_id_existing:
                    product_svc._client.put(f"products/{wc_id_existing}", {"status": "draft"})
                    logger.warning(f"  🖼️→∅ no image obtainable → draft for manual image [{product.sku}]")
                    return ("review_no_image", {"wc_id": wc_id_existing, "sku": product.sku,
                                                "name": product.display_name()[:60]})
            time.sleep(1.0)

        except WooCommerceError as exc:
            # ── duplicate/invalid SKU → product already exists → update only ──
            if _is_duplicate_sku_error(exc):
                rid = _resource_id_from_error(exc)
                existing2 = None
                if rid:
                    try:
                        existing2 = product_svc._client.get(f"products/{rid}")
                    except Exception:
                        existing2 = None
                if not existing2:
                    existing2 = product_svc.find_by_sku(product.sku)
                if existing2 and existing2.get("id"):
                    logger.info(f"  ↩️  SKU exists (ID={existing2['id']}) → update instead of create [{product.sku}]")
                    if existing2.get("status") == "trash":
                        wc_obj = product_svc.update(existing2["id"], product, category_ids,
                                                    images_payload, shipping_class=shipping_class)
                        return ("ok_restored", {"wc_id": existing2["id"], "sku": product.sku,
                                                "name": product.display_name()[:60]})
                    return update_existing_product(product, existing2, config, product_svc, stats,
                                                   category_svc=category_svc, content_generator=content_generator,
                                                   category_ids=category_ids, media_svc=media_svc, supplier=supplier)

            # ── WC rejected the remote image URL ──
            if _is_image_error(exc) and config.sync_images:
                # 1) try to download + re-host via WP media (authenticated)
                if product.images and rehosted_ids is None:
                    ids = media_svc.upload_product_images(product.images)
                    if ids:
                        rehosted_ids = ids
                        logger.info(f"  🖼️  WC rejected remote image → re-hosted {len(ids)} via WP "
                                    f"media, retrying [{product.sku}]")
                        continue
                # 2) re-host unavailable (e.g. WP media 401) → publish WITHOUT
                #    image as a draft flagged for manual image add, so the
                #    product still goes up and the supplier is not aborted.
                logger.warning(f"  🖼️→∅ WC won't fetch image & rehost unavailable → "
                               f"creating without image (draft) [{product.sku}]")
                try:
                    product.mark_for_review("WC could not fetch supplier image — draft without image")
                    product.status = STATUS_DRAFT
                    if wc_id_existing:
                        wc_obj = product_svc.update(wc_id_existing, product, category_ids, [],
                                                    shipping_class=shipping_class)
                    else:
                        wc_obj = product_svc.create(product, category_ids, [],
                                                    shipping_class=shipping_class)
                    return ("review_no_image", {"wc_id": wc_obj.get("id"), "sku": product.sku,
                                                "name": product.display_name()[:60]})
                except WooCommerceError as exc2:
                    if _is_duplicate_sku_error(exc2):
                        ex = product_svc.find_by_sku(product.sku)
                        if ex and ex.get("id"):
                            return update_existing_product(product, ex, config, product_svc, stats,
                                                           category_svc=category_svc, content_generator=content_generator,
                                                           category_ids=category_ids, media_svc=media_svc, supplier=supplier)
                    last_problems = [f"no-image create failed: {exc2}"]

            last_problems = [f"exception: {exc}"]
            logger.warning(f"  ⚠️  attempt {attempt}/{MAX_ATTEMPTS} [{product.sku}] error: {exc}")
            time.sleep(1.5)
        except Exception as exc:
            # Server-side 5xx (often a timeout while WC sideloads a slow/large
            # supplier image). Retrying the same image just burns minutes and
            # usually fails identically → publish WITHOUT image as a draft
            # flagged for manual image, so the product still goes up.
            if _is_server_5xx(exc) and config.sync_images and product.images \
                    and not restoring and not getattr(product, "_no_image_tried", False):
                product._no_image_tried = True
                logger.warning(f"  🖼️→∅ server 5xx (image sideload likely) → creating without "
                               f"image (draft) [{product.sku}]")
                try:
                    product.mark_for_review("Server 5xx on image sideload — draft without image")
                    product.status = STATUS_DRAFT
                    if wc_id_existing:
                        wc_obj = product_svc.update(wc_id_existing, product, category_ids, [],
                                                    shipping_class=shipping_class)
                    else:
                        wc_obj = product_svc.create(product, category_ids, [],
                                                    shipping_class=shipping_class)
                    return ("review_no_image", {"wc_id": wc_obj.get("id"), "sku": product.sku,
                                                "name": product.display_name()[:60]})
                except WooCommerceError as exc2:
                    if _is_duplicate_sku_error(exc2):
                        ex = product_svc.find_by_sku(product.sku)
                        if ex and ex.get("id"):
                            return update_existing_product(product, ex, config, product_svc, stats,
                                                           category_svc=category_svc, content_generator=content_generator,
                                                           category_ids=category_ids, media_svc=media_svc, supplier=supplier)
                    last_problems = [f"no-image create failed: {exc2}"]
                except Exception as exc2:
                    last_problems = [f"no-image create failed: {exc2}"]
            last_problems = [f"exception: {exc}"]
            logger.warning(f"  ⚠️  attempt {attempt}/{MAX_ATTEMPTS} [{product.sku}] error: {exc}")
            time.sleep(1.5)

    return ("fail", {"sku": product.sku, "name": product.name[:60],
                     "problems": last_problems, "attempts": MAX_ATTEMPTS})


# ──────────────────────────────────────────────────────────────────
# Run one supplier
# ──────────────────────────────────────────────────────────────────

def run_supplier(key, config, svc_bundle, sku_filter=None):
    (settings, product_svc, media_svc, category_svc, category_matcher,
     content_generator, shipping_class_map, http_factory) = svc_bundle

    sup_report = {
        "key": key, "name": config.supplier_name,
        "scraped": 0, "created": 0, "updated": 0, "restored": 0,
        "fixed": 0, "review_no_image": 0,
        "out_of_stock": 0, "failed": 0, "skipped_after_abort": 0,
        "aborted": False, "abort_reason": "", "failures": [], "warnings": [], "review": [],
    }
    stats = {"out_of_stock": 0}

    logger.info(f"\n{'='*60}\nSUPPLIER: {config.supplier_name} [{key}]\n{'='*60}")

    http = http_factory()
    try:
        supplier = build_supplier(key, config, http)
        products = supplier.run()
    except Exception as exc:
        logger.error(f"[{key}] scrape failed: {exc}", exc_info=True)
        sup_report["aborted"] = True
        sup_report["abort_reason"] = f"scrape failed: {exc}"
        return sup_report

    # filter zero/no price
    products = [p for p in products if p.price and p.price > 0]
    # optional: only process a specific set of SKUs (targeted retry)
    if sku_filter:
        products = [p for p in products if p.sku in sku_filter]
        logger.info(f"[{key}] SKU filter active → {len(products)} of {len(sku_filter)} target SKUs found in scrape")
    sup_report["scraped"] = len(products)
    if settings.max_products_per_supplier > 0:
        products = products[: settings.max_products_per_supplier]
    logger.info(f"[{key}] processing {len(products)} priced products")

    for idx, product in enumerate(products):
        try:
            status, detail = process_product(
                product, config, product_svc, media_svc, category_svc,
                category_matcher, content_generator, shipping_class_map, stats,
                supplier=supplier)
        except Exception as exc:
            logger.error(f"[{key}] unhandled error for {product.sku}: {exc}", exc_info=True)
            status, detail = "fail", {"sku": product.sku, "name": product.name[:60],
                                      "problems": [f"unhandled: {exc}"]}

        if status == "ok_created":
            sup_report["created"] += 1
            if detail.get("warnings"):
                sup_report["warnings"].append({"sku": detail["sku"], "w": detail["warnings"]})
            logger.info(f"  ✅ CREATED [{detail['sku']}] {detail['name']} → WC {detail['wc_id']} "
                        f"(attempt {detail.get('attempt')})")
        elif status == "ok_restored":
            sup_report["restored"] += 1
            logger.info(f"  ♻️  RESTORED [{detail['sku']}] {detail['name']} → WC {detail['wc_id']}")
        elif status == "ok_updated":
            sup_report["updated"] += 1
            if any("tags" in c or "category" in c for c in detail.get("changes", [])):
                sup_report["fixed"] += 1
            logger.info(f"  🔄 UPDATED [{detail['sku']}] {detail['name']} → {', '.join(detail['changes'])}")
        elif status == "review_no_image":
            sup_report["created"] += 1
            sup_report["review_no_image"] += 1
            sup_report["review"].append({"sku": detail["sku"], "wc_id": detail.get("wc_id"),
                                          "reason": "no image (WC rejected supplier URL)"})
            logger.warning(f"  📝 CREATED-NO-IMAGE [{detail['sku']}] {detail['name']} → WC "
                           f"{detail['wc_id']} (draft, needs manual image)")
        elif status == "fail":
            # ── user choice: skip the bad product, keep going (do NOT abort) ──
            sup_report["failed"] += 1
            sup_report["failures"].append(detail)
            logger.error(f"  ⏭️  SKIPPED after {MAX_ATTEMPTS} attempts [{detail['sku']}] "
                         f"{detail['name']}: {', '.join(detail.get('problems', []))} — continuing")

    sup_report["out_of_stock"] = stats["out_of_stock"]
    logger.info(
        f"[{key}] done: created={sup_report['created']} updated={sup_report['updated']} "
        f"fixed={sup_report['fixed']} restored={sup_report['restored']} "
        f"review_no_image={sup_report['review_no_image']} oos={sup_report['out_of_stock']} "
        f"failed={sup_report['failed']}")
    return sup_report


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--supplier", help="run supplier key(s), comma-separated")
    parser.add_argument("--only-enabled", action="store_true", default=True)
    parser.add_argument("--skus", help="only process these SKUs (comma-separated) — targeted retry")
    parser.add_argument("--from-report", help="path to a verify_*.json; retry every SKU in its failures lists")
    args = parser.parse_args()

    # ── build optional per-supplier SKU filter (targeted retry of skips) ──
    sku_filter_by_key = {}
    if args.from_report:
        rep = json.loads(Path(args.from_report).read_text(encoding="utf-8"))
        for s in rep.get("suppliers", []):
            skus = {f.get("sku") for f in s.get("failures", []) if f.get("sku")}
            if skus:
                sku_filter_by_key[s["key"]] = skus
    if args.skus:
        manual = {x.strip() for x in args.skus.split(",") if x.strip()}
        # applies to whichever --supplier keys are given
        for k in (args.supplier or "").split(","):
            k = k.strip()
            if k:
                sku_filter_by_key[k] = manual

    settings = load_app_settings()
    logger.info(f"WC={settings.woocommerce_url} dry_run={settings.dry_run}")

    wc_client = WooCommerceClient(
        url=settings.woocommerce_url, consumer_key=settings.woocommerce_key,
        consumer_secret=settings.woocommerce_secret, wp_user=settings.wp_user,
        wp_app_password=settings.wp_app_password, verify_ssl=settings.verify_ssl,
        dry_run=settings.dry_run)

    product_svc = ProductService(wc_client)
    media_svc = MediaService(wc_client, verify_ssl=settings.verify_ssl)
    category_svc = CategoryService(wc_client)
    logger.info("Loading WooCommerce categories...")
    category_svc.load()

    # ── Warm the SKU cache from EVERY product on the store (incl. trash) ──
    # The bare /products?sku=X endpoint intermittently misses existing SKUs,
    # which made create() fail with "duplicate SKU" and (under the 5-retry
    # rule) abort the whole supplier. Pre-loading every SKU once fixes that
    # at the root and makes find_by_sku O(1).
    logger.info("Warming SKU cache (all products + trash)...")
    warmed = 0
    for status in ("any", "trash"):
        try:
            for p in wc_client.get_all("products", status=status):
                sku = p.get("sku")
                if sku and sku not in product_svc._sku_cache:
                    product_svc._sku_cache[sku] = p
                    warmed += 1
        except Exception as exc:
            logger.warning(f"SKU warm ({status}) failed: {exc}")
    logger.info(f"SKU cache warmed with {warmed} products")

    category_matcher = CategoryMatcher(
        category_mapping=load_category_mapping(),
        keyword_rules=load_keyword_rules(),
        wc_categories=category_svc.category_names)
    shipping_class_map = load_shipping_class_mapping()

    ai_client = OpenAIClient(api_key=settings.openai_api_key, model=settings.openai_model)
    content_generator = ProductContentGenerator(ai_client)

    from src.scraping.http_client import HttpClient
    http_factory = lambda: HttpClient(verify_ssl=settings.verify_ssl,
                                       request_delay=settings.request_delay)

    svc_bundle = (settings, product_svc, media_svc, category_svc, category_matcher,
                  content_generator, shipping_class_map, http_factory)

    all_suppliers = load_suppliers()
    if args.from_report and not args.supplier:
        keys = list(sku_filter_by_key.keys())
    elif args.supplier:
        keys = [k.strip() for k in args.supplier.split(",") if k.strip()]
    else:
        keys = [k for k, c in all_suppliers.items() if c.enabled]

    logger.info(f"Suppliers to run: {keys}")
    if sku_filter_by_key:
        logger.info(f"Targeted retry — SKU counts: { {k: len(v) for k, v in sku_filter_by_key.items()} }")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    reports = []
    for key in keys:
        config = all_suppliers[key]
        rep = run_supplier(key, config, svc_bundle, sku_filter=sku_filter_by_key.get(key))
        reports.append(rep)
        # persist after each supplier (resumable view)
        out = Path(__file__).resolve().parent / "data" / "reports" / f"verify_{run_id}.json"
        out.write_text(json.dumps(
            {"run_id": run_id, "suppliers": reports}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    # final summary
    print("\n" + "#" * 60)
    print(f"  VERIFY-RUN SUMMARY  {run_id}")
    print("#" * 60)
    for r in reports:
        flag = "  ⛔SCRAPE-FAILED" if r["aborted"] else ""
        print(f"  {r['name']:<12} created={r['created']:<4} updated={r['updated']:<5} "
              f"fixed={r.get('fixed', 0):<4} no-image-draft={r.get('review_no_image', 0):<3} "
              f"oos={r['out_of_stock']:<3} failed={r['failed']:<3}{flag}")
        if r["aborted"]:
            print(f"       reason: {r['abort_reason']}")
        if r["failed"]:
            for f in r["failures"][:10]:
                print(f"       ⏭️  skipped {f['sku']}: {', '.join(f.get('problems', []))}")
    print("#" * 60)
    print(f"report: data/reports/verify_{run_id}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
