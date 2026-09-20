# -*- coding: utf-8 -*-
"""
ART Judaica (israel-judaica.com) → store sync.

Pipeline:
  1 log in to the B2B site           (src/suppliers/israel_judaica_supplier)
  2 fetch every product (getProducts)
  3 dedup by SKU
  4 classify PER PRODUCT by name into the closest existing store category
    (src/enrichment/product_classifier — same engine as Golyan)
  5 price = B2B price ×1.8  (owner: +80%)
  6 create/update by SKU; images downloaded via the session and uploaded to WP
    (hot-link protection blocks WC from side-loading ART image URLs directly)
  7 reports + classification json for the review page

    python art_sync.py            # DRY-RUN: classify + report, no writes
    python art_sync.py --apply    # upload
    python art_sync.py --limit N  # cap (testing)

Env: IJ_USER, IJ_PASSWORD (B2B login) + the usual WOOCOMMERCE_*/WP_*/OPENAI_*.
"""
import sys, io, os, math, time, collections, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
import warnings; warnings.filterwarnings("ignore")
from dotenv import load_dotenv; load_dotenv()
import re as _re

from src.core.config_loader import load_app_settings, load_shipping_class_mapping
from src.core.constants import META_SYNC_MANAGED
from src.core.utils import stable_sku
from src.enrichment.openai_client import OpenAIClient
from src.enrichment.product_content_generator import ProductContentGenerator
from src.enrichment.product_classifier import ProductClassifier
from src.models.product import SupplierProduct
from src.woocommerce.client import WooCommerceClient
from src.woocommerce.category_service import CategoryService
from src.woocommerce.product_service import ProductService
from config.golyan_mapping import FURNITURE_TARGETS, CLASSIFIER_HINTS
from src.suppliers import israel_judaica_supplier as ij

SUPPLIER_KEY = "art"
SKU_PREFIX = "ART"
PRICE_MULT = 1.8                    # owner: ART price ×1.8 (+80%)
REVIEW_FALLBACK_CAT = "בדיקה ידנית"

# Judaica-specific hints on top of the shared ones.
ART_HINTS = CLASSIFIER_HINTS + [
    "מזוזה / בית מזוזה / קלף → מזוזות.",
    "חנוכיה / מנורת חנוכה / חנוכייה → חנוכה.",
    "בקבוק / כוס / גביע קידוש → כוסות קידוש.",
    "כיסוי חלה / מפת שבת → אם אין קטגוריה מדויקת, הצע קטגוריה חדשה מתאימה.",
    "שרשרת / תליון / תכשיט / צמיד / עגילים → אם אין קטגוריית תכשיטים, הצע קטגוריה חדשה 'תכשיטים'.",
]

_SUPPLIER_WORDS = _re.compile(
    r"art\s*judaica|ישראל\s*יודאיקה|israel[\s-]*judaica|\bA\.?J\.?\b|\bART\b", _re.IGNORECASE)

APPLY = "--apply" in sys.argv
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0


def _strip_supplier(text: str) -> str:
    if not text:
        return text
    t = _SUPPLIER_WORDS.sub("", text)
    return _re.sub(r"\s{2,}", " ", t).strip(" -–,|\"")


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
    ij_user = os.getenv("IJ_USER", ""); ij_pass = os.getenv("IJ_PASSWORD", "")
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)
    cat_svc = CategoryService(c); cat_svc.load()

    print(f"=== ART SYNC ({'APPLY' if APPLY else 'DRY-RUN'}) ===", flush=True)
    session = ij.login(ij_user, ij_pass)
    if not session:
        print("!! ART login failed — check IJ_USER / IJ_PASSWORD", flush=True)
        return
    products = ij.fetch_all(session)
    # Sanity guard: a throttled / not-fully-authenticated session returns
    # products priced at 0. Never upload zero-price products.
    priced = sum(1 for p in products if p["price"] > 0)
    if products and priced < 0.5 * len(products):
        print(f"!! only {priced}/{len(products)} products have a B2B price — session not "
              f"price-authenticated (likely rate-limited). Aborting without writes.", flush=True)
        return
    if LIMIT:
        products = products[:LIMIT]
    print(f"fetched: {len(products)} (priced: {priced})", flush=True)

    # dedup (already deduped in fetch_all, but keep the reporting parity)
    by_sku = {p["sku"]: p for p in products if p["sku"]}
    print(f"unique SKUs: {len(by_sku)}", flush=True)

    # leaf categories from the live store tree
    raw_cats = get_all_resilient(c, "products/categories")
    cat_by_id = {rc["id"]: rc for rc in raw_cats}
    parent_ids = {rc.get("parent") for rc in raw_cats if rc.get("parent")}
    leaves = [{"name": rc["name"],
               "parent": (cat_by_id.get(rc.get("parent"), {}).get("name", "") if rc.get("parent") else "")}
              for rc in raw_cats if rc["id"] not in parent_ids]

    ai = OpenAIClient(api_key=s.openai_api_key, model=s.openai_model)
    clf = ProductClassifier(ai, leaves, hints=ART_HINTS)
    print(f"classifying {len(by_sku)} products by name ({len(leaves)} leaf categories)…", flush=True)
    cls = clf.classify([{"sku": sk, "name": pp["name"]} for sk, pp in by_sku.items()]) if clf.available else {}

    dist = collections.Counter(); needs_review = []
    new_suggest = collections.defaultdict(list); low_conf = []; price_samples = []; review_rows = []
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
        conf = (r.confidence if r else 0.0)
        if not review and conf and conf < 0.55:
            low_conf.append((sku, p["name"], target, conf))
        if len(price_samples) < 10:
            price_samples.append((p["name"][:30], p["price"], p["_price"]))
        review_rows.append({"sku": sku, "name": p["name"], "target": target,
                            "confidence": round(conf, 2), "review": review,
                            "is_new": bool(r.is_new) if r else False,
                            "new_name": (r.new_name if r else "")})

    try:
        with open("art_classification.json", "w", encoding="utf-8") as f:
            json.dump({"rows": review_rows,
                       "new_suggestions": [{"new_name": nn, "parent": pr, "count": len(v),
                                            "samples": [x for x, _ in v[:8]]}
                                           for (nn, pr), v in sorted(new_suggest.items(), key=lambda kv: -len(kv[1]))]},
                      f, ensure_ascii=False)
    except Exception as exc:
        print(f"  (classification json not written: {exc})", flush=True)

    # ── reports ──
    print("\n── category distribution ──", flush=True)
    for t, n in dist.most_common():
        note = "  ⚠️ בדיקה ידנית" if t == REVIEW_FALLBACK_CAT else ""
        print(f"  {n:4}  {t}{note}", flush=True)
    print("\n── price samples (B2B → ×1.8) ──", flush=True)
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
          f"low-conf={len(low_conf)} ({'APPLIED' if APPLY else 'DRY-RUN'}) ===", flush=True)

    if not APPLY:
        print("\n(DRY-RUN — no writes.)", flush=True)
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
        clean_name = _strip_supplier(p["name"])
        cat_ids = cat_svc.resolve_list(p["_target"])
        ship = shipping_map.get(p["_target"], "")
        prod = SupplierProduct(
            supplier_name="ART", supplier_key=SUPPLIER_KEY, supplier_product_id=src_sku,
            supplier_url="", sku=sku, name=clean_name, original_description="",
            price=p["price"], stock_status="instock", is_available=True,
            supplier_category="", mapped_category=p["_target"], images=p["images"],
            status="publish",
        )
        prod.calculated_price = p["_price"]
        try:
            if existing:
                # FAST path — only fix category/price/shipping; keep existing
                # text + images (no OpenAI, no image re-upload).
                prod.ai_generated = False
                product_svc.update(existing["id"], prod, cat_ids, [], shipping_class=ship)
                updated += 1
            else:
                # NEW — download image via session, upload to WP, enrich text.
                media = []
                img = ij.download_image(session, p["image_url"])
                if img:
                    mid = c.upload_media(img, f"{src_sku}.jpg", "image/jpeg")
                    if mid:
                        media = [{"id": mid}]
                if not media:
                    prod.mark_for_review("No image from ART"); no_image += 1
                if content_gen.available:
                    try:
                        content_gen.enrich(prod)
                    except Exception as exc:
                        print(f"   enrich fail [{sku}]: {str(exc)[:60]}", flush=True)
                prod.improved_name = _strip_supplier(prod.improved_name) or clean_name
                prod.full_description = _strip_supplier(prod.full_description)
                prod.ai_generated = True
                product_svc.create(prod, cat_ids, media, shipping_class=ship)
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
