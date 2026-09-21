# -*- coding: utf-8 -*-
"""
M.M.S → store sync.

Source: a Dropbox folder of product images with the data burned into a bottom
banner (src/suppliers/mms_supplier.py OCRs + crops each). Price = banner price
×1.7, per-product name classification into the store's categories, dimensions
kept in the description, the clean (banner-removed) image uploaded to WP media,
supplier name "M.M.S" stored only in meta next to the SKU. Upsert by SKU.

Env: MMS_DROPBOX_URL, + WOOCOMMERCE_*/WP_*/OPENAI_*.
    python mms_sync.py                 # DRY-RUN (OCR + classify + report)
    python mms_sync.py --apply
    python mms_sync.py --limit N
"""
import collections
import io
import math
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
import warnings

warnings.filterwarnings("ignore")
from dotenv import load_dotenv

load_dotenv()

from config.golyan_mapping import CLASSIFIER_HINTS
from src.core.config_loader import load_app_settings, load_shipping_class_mapping
from src.core.constants import META_SYNC_MANAGED
from src.enrichment.openai_client import OpenAIClient
from src.enrichment.product_classifier import ProductClassifier
from src.enrichment.product_content_generator import ProductContentGenerator
from src.models.product import SupplierProduct
from src.suppliers import mms_supplier as mms
from src.woocommerce.category_service import CategoryService
from src.woocommerce.client import WooCommerceClient
from src.woocommerce.product_service import ProductService

SUPPLIER_NAME = "M.M.S"
SKU_PREFIX = "MMS"
PRICE_MULT = 1.7
REVIEW_FALLBACK_CAT = "מוצרים נוספים"
MMS_HINTS = CLASSIFIER_HINTS + [
    "תמונה / קנבס / תמונה ממוסגרת → תמונות קיר.",
    "פסל בטון / דמוי אבן / אומנות בבטון → פסלים.",
    "אגרטל / ואזה / כד → אגרטלים וואזות.",
    "ספסל / הדום → ספסלים או הדומים לפי המוצר.",
    "בית עציץ / מעמד עציץ → בתי עציץ.",
    "ענף / זר / פרח / צמח / ירק מלאכותי / עלים → צמחים.",
    "מדף / כוננית / מדף צף → ריהוט וכונניות מדפים או המדף המתאים.",
]

APPLY = "--apply" in sys.argv
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0


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


def price_for(src):
    return int(math.ceil(src * PRICE_MULT))


def main():
    dbx = os.getenv("MMS_DROPBOX_URL", "").strip()
    if not dbx:
        print("MMS_DROPBOX_URL not set.", flush=True); sys.exit(1)

    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)
    cat_svc = CategoryService(c); cat_svc.load()
    ai = OpenAIClient(api_key=s.openai_api_key, model=s.openai_model)

    from openai import OpenAI
    ocr_cli = OpenAI(api_key=s.openai_api_key)     # raw client for vision OCR

    print(f"=== M.M.S SYNC ({'APPLY' if APPLY else 'DRY-RUN'}) ===", flush=True)
    products = mms.fetch_all(dbx, ocr_cli, ocr_model="gpt-4o-mini", max_items=LIMIT)
    by_sku = {p["sku"]: p for p in products if p["sku"]}
    print(f"products: {len(products)} | unique SKUs: {len(by_sku)}", flush=True)

    # classify by name
    raw_cats = get_all_resilient(c, "products/categories")
    by_id = {rc["id"]: rc for rc in raw_cats}
    parent_ids = {rc.get("parent") for rc in raw_cats if rc.get("parent")}
    leaves = [{"name": rc["name"],
               "parent": (by_id.get(rc.get("parent"), {}).get("name", "") if rc.get("parent") else "")}
              for rc in raw_cats if rc["id"] not in parent_ids]
    clf = ProductClassifier(ai, leaves, hints=MMS_HINTS)
    cls = clf.classify([{"sku": sk, "name": p["name"]} for sk, p in by_sku.items()]) if clf.available else {}

    dist = collections.Counter(); needs_review = []; price_samples = []
    for sku, p in by_sku.items():
        r = cls.get(sku)
        target = (r.category if r else "") or ""
        if not target:
            target = REVIEW_FALLBACK_CAT; needs_review.append(p)
        dist[target] += 1
        p["_target"] = target
        p["_price"] = price_for(p["price"])
        if len(price_samples) < 12:
            price_samples.append((p["name"][:24], p["price"], p["_price"], target))

    print("\n── category distribution ──", flush=True)
    for t, n in dist.most_common():
        print(f"  {n:4}  {t}", flush=True)
    print("\n── samples (name | banner→×1.7 | category | dims) ──", flush=True)
    for sku, p in list(by_sku.items())[:12]:
        print(f"  {p['name'][:26]:<26} {p['price']:>6.0f}→{p['_price']:>6} | {p['_target']} | {p['dimensions']}", flush=True)
    print(f"\n=== SUMMARY: products={len(by_sku)} classified={len(by_sku)-len(needs_review)} "
          f"needs-review={len(needs_review)} ({'APPLIED' if APPLY else 'DRY-RUN'}) ===", flush=True)

    if not APPLY:
        print("\n(DRY-RUN — no writes.)", flush=True)
        return
    if clf.available and by_sku and len(needs_review) > 0.6 * len(by_sku):
        print(f"\n🛑 ABORT: classification failed for {len(needs_review)}/{len(by_sku)} (OpenAI?). No writes.", flush=True)
        return

    # ═══════════════ APPLY ═══════════════
    product_svc = ProductService(c)
    shipping_map = load_shipping_class_mapping()
    content_gen = ProductContentGenerator(ai)

    print("warming SKU cache…", flush=True)
    for p in get_all_resilient(c, "products", status="any"):
        if p.get("sku"):
            product_svc._sku_cache[p["sku"]] = p

    created = updated = failed = 0
    for src_sku, p in by_sku.items():
        sku = f"{SKU_PREFIX}-{src_sku}"
        existing = product_svc.find_by_sku(sku)
        cat_ids = cat_svc.resolve_list(p["_target"])
        ship = shipping_map.get(p["_target"], "")
        dims = p.get("dimensions", "")
        desc0 = f"מידות: כ־{dims}" if dims else ""
        prod = SupplierProduct(
            supplier_name=SUPPLIER_NAME, supplier_key="mms", supplier_product_id=src_sku,
            supplier_url="", sku=sku, name=p["name"], original_description=desc0,
            price=p["price"], stock_status="instock", is_available=True,
            supplier_category=p.get("source_cat", ""), mapped_category=p["_target"],
            images=[], status="publish",
        )
        prod.calculated_price = p["_price"]
        try:
            if existing:
                prod.ai_generated = False
                product_svc.update(existing["id"], prod, cat_ids, [], shipping_class=ship)
                updated += 1
            else:
                # upload the clean (banner-removed) image to WP media
                media_id = c.upload_media(p["image_bytes"], p["image_filename"], "image/jpeg")
                images_payload = [{"id": media_id}] if media_id else []
                if content_gen.available:
                    try:
                        content_gen.enrich(prod)
                    except Exception as exc:
                        print(f"   enrich fail [{sku}]: {str(exc)[:50]}", flush=True)
                prod.ai_generated = True
                # guarantee the dimensions are present in the final description
                if dims and dims not in (prod.full_description or ""):
                    prod.full_description = (prod.full_description or "") + f"\n\nמידות: כ־{dims}"
                product_svc.create(prod, cat_ids, images_payload, shipping_class=ship)
                created += 1
            if (created + updated) % 25 == 0:
                print(f"   …{created} created, {updated} updated", flush=True)
        except Exception as exc:
            failed += 1
            print(f"   FAIL [{sku}] {p['name'][:26]}: {str(exc)[:70]}", flush=True)

    print(f"\n=== APPLIED. created={created} updated={updated} failed={failed} ===", flush=True)


if __name__ == "__main__":
    main()
