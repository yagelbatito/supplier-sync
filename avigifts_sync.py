# -*- coding: utf-8 -*-
"""
Avi Gifts (avigifts.co.il) → store sync.

Public WooCommerce Store API (no login). Per-product name classification into
the closest existing store category (shared classifier), price = site price
×1.8 (owner), upsert by SKU. Images are public so WooCommerce side-loads them
directly. New products get an OpenAI rewrite + footer + supplier-name scrub;
existing products take the fast path (category/price/shipping only).

    python avigifts_sync.py            # DRY-RUN: classify + report, no writes
    python avigifts_sync.py --apply    # upload
    python avigifts_sync.py --limit N  # cap (testing)
"""
import collections
import io
import json
import math
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
import warnings

warnings.filterwarnings("ignore")
import re as _re

from dotenv import load_dotenv

load_dotenv()

from config.golyan_mapping import CLASSIFIER_HINTS
from src.core.config_loader import load_app_settings, load_shipping_class_mapping
from src.core.constants import META_SYNC_MANAGED
from src.core.utils import stable_sku
from src.enrichment.openai_client import OpenAIClient
from src.enrichment.product_classifier import ProductClassifier
from src.enrichment.product_content_generator import ProductContentGenerator
from src.models.product import SupplierProduct
from src.suppliers import avigifts_supplier as avi
from src.woocommerce.category_service import CategoryService
from src.woocommerce.client import WooCommerceClient
from src.woocommerce.product_service import ProductService

SUPPLIER_KEY = "avigifts"
SKU_PREFIX = "AVI"
PRICE_MULT = 1.8                   # owner: Avi Gifts site price ×1.8 (+80%)
REVIEW_FALLBACK_CAT = "בדיקה ידנית"

_SUPPLIER_WORDS = _re.compile(r"אבי\s*מתנות|avi\s*gifts|avigifts", _re.IGNORECASE)

APPLY = "--apply" in sys.argv
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0


def _strip_supplier(text: str) -> str:
    if not text:
        return text
    t = _SUPPLIER_WORDS.sub("", text)
    return _re.sub(r"\s{2,}", " ", t).strip(" -–,|\"")


def _clean_name(name: str) -> str:
    """Strip the supplier's internal price/pack codes that prefix Avi Gifts
    names (e.g. '10.8 60יח שעון קיר…' → 'שעון קיר…', '5.9סט אוכל' → 'סט אוכל').
    Removes a leading decimal price code and a leading 'NNיח' pack token; keeps
    real content. The OpenAI rewrite cleans the rest — this guards the fallback."""
    if not name:
        return name
    n = name
    for _ in range(3):
        n = _re.sub(r"^\s*\d+[.,]\d+\s*", "", n)          # leading decimal price
        n = _re.sub(r"^\s*\d+\s*יח['\"׳]?\s*", "", n)     # leading NNיח pack code
    return n.strip(" -–,|\"")


def _norm(n: str) -> str:
    return " ".join((n or "").split()).strip().lower()


def get_all_resilient(c, endpoint, per_page=100, **params):
    results, page = [], 1
    while True:
        batch = None
        for attempt in range(5):
            try:
                batch = c.get(endpoint, params={"per_page": per_page, "page": page, **params})
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2 * (attempt + 1))
        if not batch:
            break
        results.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return results


def price_for(src_price: float) -> int:
    return int(math.ceil(src_price * PRICE_MULT))


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)
    cat_svc = CategoryService(c); cat_svc.load()

    print(f"=== AVI GIFTS SYNC ({'APPLY' if APPLY else 'DRY-RUN'}) ===", flush=True)
    products = avi.fetch_all(max_pages=(LIMIT // 100 + 1) if LIMIT else 0)
    if LIMIT:
        products = products[:LIMIT]
    print(f"fetched: {len(products)}", flush=True)

    by_sku = {}
    for p in products:
        if p["sku"] and p["sku"] not in by_sku:
            by_sku[p["sku"]] = p
    print(f"unique SKUs: {len(by_sku)}", flush=True)

    raw_cats = get_all_resilient(c, "products/categories")
    cat_by_id = {rc["id"]: rc for rc in raw_cats}
    parent_ids = {rc.get("parent") for rc in raw_cats if rc.get("parent")}
    leaves = [{"name": rc["name"],
               "parent": (cat_by_id.get(rc.get("parent"), {}).get("name", "") if rc.get("parent") else "")}
              for rc in raw_cats if rc["id"] not in parent_ids]

    ai = OpenAIClient(api_key=s.openai_api_key, model=s.openai_model)
    clf = ProductClassifier(ai, leaves, hints=CLASSIFIER_HINTS)
    print(f"classifying {len(by_sku)} products by name ({len(leaves)} leaf categories)…", flush=True)
    cls = clf.classify([{"sku": sk, "name": pp["name"]} for sk, pp in by_sku.items()]) if clf.available else {}

    dist = collections.Counter(); needs_review = []
    new_suggest = collections.defaultdict(list); price_samples = []; review_rows = []
    for sku, p in by_sku.items():
        r = cls.get(sku)
        target = (r.category if r else "") or ""
        review = False
        if not target:
            target = REVIEW_FALLBACK_CAT; review = True; needs_review.append(p)
        if r and r.is_new and r.new_name:
            new_suggest[(r.new_name, r.parent or "")].append((sku, p["name"]))
        dist[target] += 1
        p["_target"] = target
        p["_price"] = price_for(p["price"])
        if len(price_samples) < 10:
            price_samples.append((p["name"][:30], p["price"], p["_price"]))
        review_rows.append({"sku": sku, "name": p["name"], "target": target,
                            "confidence": round(r.confidence if r else 0.0, 2), "review": review,
                            "is_new": bool(r.is_new) if r else False, "new_name": (r.new_name if r else "")})

    try:
        with open("avigifts_classification.json", "w", encoding="utf-8") as f:
            json.dump({"rows": review_rows,
                       "new_suggestions": [{"new_name": nn, "parent": pr, "count": len(v),
                                            "samples": [x for x, _ in v[:8]]}
                                           for (nn, pr), v in sorted(new_suggest.items(), key=lambda kv: -len(kv[1]))]},
                      f, ensure_ascii=False)
    except Exception as exc:
        print(f"  (classification json not written: {exc})", flush=True)

    print("\n── category distribution ──", flush=True)
    for t, n in dist.most_common():
        note = "  ⚠️ בדיקה ידנית" if t == REVIEW_FALLBACK_CAT else ""
        print(f"  {n:4}  {t}{note}", flush=True)
    print("\n── price samples (site → ×1.8) ──", flush=True)
    for name, src, new in price_samples:
        print(f"  {name:<30} {src:>7.0f} → {new:>6}", flush=True)
    print(f"\n── NEEDS_REVIEW → '{REVIEW_FALLBACK_CAT}': {len(needs_review)} ──", flush=True)
    for p in needs_review[:15]:
        print(f"  [{p['sku']}] {p['name'][:46]}", flush=True)
    if len(needs_review) > 15:
        print(f"  … +{len(needs_review) - 15} more", flush=True)
    print(f"\n── NEW_CATEGORY_SUGGESTIONS: {len(new_suggest)} ──", flush=True)
    for (nn, pr), items in sorted(new_suggest.items(), key=lambda kv: -len(kv[1]))[:15]:
        print(f"  '{nn}' תחת '{pr or '—'}': {len(items)}  דוגמאות: {[x for x,_ in items[:3]]}", flush=True)

    mapped_ok = sum(dist.values()) - len(needs_review)
    print(f"\n=== SUMMARY: fetched={len(products)} unique={len(by_sku)} classified={mapped_ok} "
          f"needs-review={len(needs_review)} new-suggested={len(new_suggest)} "
          f"({'APPLIED' if APPLY else 'DRY-RUN'}) ===", flush=True)

    if not APPLY:
        print("\n(DRY-RUN — no writes.)", flush=True)
        return

    # SAFETY: don't let a wholesale classification failure (OpenAI out of
    # credits / API down) overwrite good categories with "בדיקה ידנית".
    if clf.available and by_sku and len(needs_review) > 0.6 * len(by_sku):
        print(f"\n🛑 ABORT: classification failed for {len(needs_review)}/{len(by_sku)} products "
              f"(likely OpenAI credits/API error). No writes — existing categories preserved.", flush=True)
        return

    # ═══════════════ APPLY ═══════════════
    product_svc = ProductService(c)
    shipping_map = load_shipping_class_mapping()
    content_gen = ProductContentGenerator(OpenAIClient(api_key=s.openai_api_key, model=s.openai_model))

    print("warming SKU cache + name index…", flush=True)
    existing_names = set()
    for p in get_all_resilient(c, "products", status="any"):
        sk = p.get("sku")
        if sk:
            product_svc._sku_cache[sk] = p
        meta = {m["key"]: m["value"] for m in p.get("meta_data", [])}
        if str(meta.get(META_SYNC_MANAGED, "")).lower() in ("true", "1"):
            existing_names.add(_norm(p.get("name", "")))

    created = updated = dup_name = failed = no_image = 0
    for src_sku, p in by_sku.items():
        if "_target" not in p:
            continue
        sku = stable_sku(supplier_key=SUPPLIER_KEY, product_id=src_sku, product_url="", prefix=SKU_PREFIX)
        existing = product_svc.find_by_sku(sku)
        if not existing and _norm(p["name"]) in existing_names:
            dup_name += 1
            continue
        available = p["in_stock"]
        clean_name = _clean_name(_strip_supplier(p["name"]))
        clean_desc = _strip_supplier(p["description"].replace(src_sku, "")) if p["description"] else ""
        cat_ids = cat_svc.resolve_list(p["_target"])
        ship = shipping_map.get(p["_target"], "")
        prod = SupplierProduct(
            supplier_name="AviGifts", supplier_key=SUPPLIER_KEY, supplier_product_id=src_sku,
            supplier_url="", sku=sku, name=clean_name, original_description=clean_desc,
            price=p["price"], stock_status="instock" if available else "outofstock",
            is_available=available, supplier_category=", ".join(p["source_cats"]),
            mapped_category=p["_target"], images=p["images"],
            status="publish" if available else "draft",
        )
        prod.calculated_price = p["_price"]
        try:
            if existing:
                prod.ai_generated = False
                product_svc.update(existing["id"], prod, cat_ids, [], shipping_class=ship)
                updated += 1
            else:
                if content_gen.available:
                    try:
                        content_gen.enrich(prod)
                    except Exception as exc:
                        print(f"   enrich fail [{sku}]: {str(exc)[:60]}", flush=True)
                prod.improved_name = _strip_supplier(prod.improved_name) or clean_name
                prod.full_description = _strip_supplier(prod.full_description)
                prod.ai_generated = True
                images_payload = [{"src": u} for u in p["images"]]
                if not images_payload:
                    prod.mark_for_review("No image from Avi Gifts"); no_image += 1
                product_svc.create(prod, cat_ids, images_payload, shipping_class=ship)
                existing_names.add(_norm(p["name"])); created += 1
            if (created + updated) % 50 == 0:
                print(f"   …{created} created, {updated} updated", flush=True)
        except Exception as exc:
            failed += 1
            print(f"   FAIL [{sku}] {p['name'][:34]}: {str(exc)[:80]}", flush=True)

    print(f"\n=== APPLIED. created={created} updated={updated} dup-name-skipped={dup_name} "
          f"no-image={no_image} failed={failed} ===", flush=True)


if __name__ == "__main__":
    main()
