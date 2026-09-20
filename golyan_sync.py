# -*- coding: utf-8 -*-
"""
Golyan → store sync (phase 1).

Pipeline (owner spec §8):
  1 fetch products from Golyan Store API   (src/suppliers/golyan_supplier.py)
  2 normalize
  3 SKU check + 4 dedup (SKU is the primary identity)
  5 exists-by-SKU?  6 map category  7 furniture?  8 price
  9 create/update   10 ensure every product has a category
  11 UNMAPPED report  12 hidden-frontend-category report  13 summary log

Pricing: furniture ×0.70 (−30%), everything else ×0.75 (−25%), decided by the
MAPPED target category (config/golyan_mapping.FURNITURE_TARGETS).

Category rules live in config/golyan_mapping.py (add rules there, not here).

    python golyan_sync.py            # DRY-RUN: reports only, no writes
    python golyan_sync.py --apply    # create categories + upsert products
    python golyan_sync.py --limit N  # cap products (testing)
"""
import sys, io, math, time, collections
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
from src.suppliers.golyan_supplier import fetch_all
from config.golyan_mapping import FURNITURE_TARGETS, NEW_CATEGORIES, CLASSIFIER_HINTS

REVIEW_FALLBACK_CAT = "בדיקה ידנית"    # where off-list products land (flagged for review)

SUPPLIER_KEY = "julian"     # keep continuity with existing JUL-* products
SKU_PREFIX = "JUL"

# Never reveal the supplier in customer-facing text.
_SUPPLIER_WORDS = _re.compile(r"גולי[איי]?ן|גולין|golyangifts|golyan|golaino", _re.IGNORECASE)


def _strip_supplier(text: str) -> str:
    if not text:
        return text
    t = _SUPPLIER_WORDS.sub("", text)
    return _re.sub(r"\s{2,}", " ", t).strip(" -–,|")


def _norm(n: str) -> str:
    return " ".join((n or "").split()).strip().lower()


def get_all_resilient(c, endpoint, per_page=100, **params):
    """Like client.get_all but retries each page on transient connection resets
    (the store intermittently drops long scans with WinError 10054)."""
    results, page = [], 1
    while True:
        batch = None
        for attempt in range(5):
            try:
                batch = c.get(endpoint, params={"per_page": per_page, "page": page, **params})
                break
            except Exception as exc:
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

APPLY = "--apply" in sys.argv
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
FURN_DISCOUNT, OTHER_DISCOUNT = 0.30, 0.25


def price_for(source_price: float, is_furniture: bool) -> int:
    factor = (1 - FURN_DISCOUNT) if is_furniture else (1 - OTHER_DISCOUNT)
    return int(math.ceil(source_price * factor))


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)
    cat_svc = CategoryService(c); cat_svc.load()
    store_cats = set(cat_svc.category_names)
    new_leaves = {leaf for leaf, _ in NEW_CATEGORIES}

    print(f"=== GOLYAN SYNC ({'APPLY' if APPLY else 'DRY-RUN'}) ===", flush=True)
    products = fetch_all(max_pages=(LIMIT // 100 + 1) if LIMIT else 0)
    if LIMIT:
        products = products[:LIMIT]
    print(f"fetched: {len(products)}", flush=True)

    # ── 3-4. SKU check + dedup ──
    by_sku, dup_source = {}, collections.Counter()
    unresolved = []
    for p in products:
        sku = p["sku"]
        if not sku:
            unresolved.append(p); continue
        if sku in by_sku:
            dup_source[sku] += 1
            continue
        by_sku[sku] = p
    print(f"unique SKUs: {len(by_sku)} | duplicate source SKUs: {len(dup_source)} | "
          f"no-SKU (unresolved): {len(unresolved)}", flush=True)

    # ── build LEAF-category candidates from the LIVE store tree ──
    raw_cats = get_all_resilient(c, "products/categories")
    cat_by_id = {rc["id"]: rc for rc in raw_cats}
    parent_ids = {rc.get("parent") for rc in raw_cats if rc.get("parent")}
    leaves = [{"name": rc["name"],
               "parent": (cat_by_id.get(rc.get("parent"), {}).get("name", "") if rc.get("parent") else "")}
              for rc in raw_cats if rc["id"] not in parent_ids]

    # ── 6-8. classify PER PRODUCT (by name) + price ──
    ai = OpenAIClient(api_key=s.openai_api_key, model=s.openai_model)
    clf = ProductClassifier(ai, leaves, hints=CLASSIFIER_HINTS)
    print(f"classifying {len(by_sku)} products by name ({len(leaves)} leaf categories)…", flush=True)
    cls = clf.classify([{"sku": sk, "name": pp["name"]} for sk, pp in by_sku.items()]) if clf.available else {}

    dist = collections.Counter()
    needs_review = []                                  # off-list → fallback category
    new_suggest = collections.defaultdict(list)        # (new_name, parent) -> [(sku,name)]
    low_conf = []
    price_samples = []
    review_rows = []                                   # for the review artifact
    for sku, p in by_sku.items():
        r = cls.get(sku)
        target = (r.category if r else "") or ""
        review = False
        if not target:                                 # off-list / no existing fit
            target = REVIEW_FALLBACK_CAT
            review = True
            needs_review.append(p)
        if r and r.is_new and r.new_name:
            new_suggest[(r.new_name, r.parent or "")].append((sku, p["name"]))
        is_furn = target in FURNITURE_TARGETS
        dist[target] += 1
        p["_target"] = target
        p["_furn"] = is_furn
        p["_price"] = price_for(p["price"], is_furn)
        conf = (r.confidence if r else 0.0)
        if not review and conf and conf < 0.55:
            low_conf.append((sku, p["name"], target, conf))
        if len(price_samples) < 10:
            price_samples.append((p["name"][:30], p["price"], p["_price"],
                                  "ריהוט −30%" if is_furn else "אחר −25%"))
        review_rows.append({"sku": sku, "name": p["name"], "target": target,
                            "confidence": round(conf, 2), "review": review,
                            "is_new": bool(r.is_new) if r else False,
                            "new_name": (r.new_name if r else ""),
                            "source_cats": p["source_cats"]})

    # persist the classification for the review page / audit
    try:
        import json as _json
        with open("golyan_classification.json", "w", encoding="utf-8") as _f:
            _json.dump({"rows": review_rows,
                        "new_suggestions": [{"new_name": nn, "parent": pr, "count": len(v),
                                             "samples": [s for s, _ in v[:8]]}
                                            for (nn, pr), v in sorted(new_suggest.items(), key=lambda kv: -len(kv[1]))]},
                       _f, ensure_ascii=False)
    except Exception as _exc:
        print(f"  (could not write classification json: {_exc})", flush=True)

    # ═══════════════ REPORTS ═══════════════
    print("\n── category distribution (per-product classifier) ──", flush=True)
    for t, n in dist.most_common():
        flag = " [ריהוט]" if t in FURNITURE_TARGETS else ""
        note = "  ⚠️ בדיקה ידנית" if t == REVIEW_FALLBACK_CAT else ""
        print(f"  {n:4}  {t}{flag}{note}", flush=True)

    print("\n── price samples (source → store) ──", flush=True)
    for name, src, new, tag in price_samples:
        print(f"  {name:<30} {src:>7.0f} → {new:>6}  ({tag})", flush=True)

    print(f"\n── NEEDS_REVIEW (no confident existing category → '{REVIEW_FALLBACK_CAT}'): {len(needs_review)} ──", flush=True)
    for p in needs_review[:20]:
        print(f"  [{p['sku']}] {p['name'][:44]}  « {', '.join(p['source_cats'][:2])}", flush=True)
    if len(needs_review) > 20:
        print(f"  … +{len(needs_review) - 20} more", flush=True)

    print(f"\n── NEW_CATEGORY_SUGGESTIONS (model proposed a new leaf): {len(new_suggest)} ──", flush=True)
    for (nn, pr), items in sorted(new_suggest.items(), key=lambda kv: -len(kv[1]))[:15]:
        print(f"  '{nn}' תחת '{pr}': {len(items)} מוצרים  דוגמאות: {[s for s,_ in items[:3]]}", flush=True)

    print(f"\n── LOW_CONFIDENCE (<0.55): {len(low_conf)} ──", flush=True)
    for sku, name, target, conf in low_conf[:15]:
        print(f"  [{sku}] {name[:38]} → {target} ({conf:.2f})", flush=True)

    if dup_source:
        print(f"\n── duplicate source SKUs: {len(dup_source)} ──", flush=True)
        for sku, n in list(dup_source.items())[:10]:
            print(f"  {sku} ×{n+1}", flush=True)

    mapped_ok = sum(dist.values()) - len(needs_review)
    print(f"\n=== SUMMARY ===", flush=True)
    print(f"  fetched={len(products)} unique={len(by_sku)} classified={mapped_ok} "
          f"needs-review={len(needs_review)} new-suggested={len(new_suggest)} "
          f"low-conf={len(low_conf)} dup-source={len(dup_source)} ({'APPLIED' if APPLY else 'DRY-RUN'})", flush=True)

    if not APPLY:
        print("\n(DRY-RUN — no writes. Review the reports above, then run --apply.)", flush=True)
        return

    # ═══════════════ APPLY ═══════════════
    product_svc = ProductService(c)
    shipping_map = load_shipping_class_mapping()
    content_gen = ProductContentGenerator(OpenAIClient(api_key=s.openai_api_key, model=s.openai_model))

    # create the missing sub-categories (spec §7)
    for leaf, parent in NEW_CATEGORIES:
        if cat_svc.resolve(leaf, fallback=""):
            continue
        pid = cat_svc.resolve(parent, fallback="")
        res = c.post("products/categories", {"name": leaf, "parent": pid or 0})
        cat_svc._categories[leaf] = res.get("id")
        print(f"  created category '{leaf}' (parent {parent})", flush=True)

    # warm SKU cache + name index (SKU is primary identity; name-dedup guards
    # a same-named item that arrives under a different SKU)
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
        # name-dedup: same name under a different SKU → skip creating a duplicate
        if not existing and _norm(p["name"]) in existing_names:
            dup_name += 1
            continue
        available = p["in_stock"]
        # scrub supplier name + the source code out of the customer-facing text
        clean_name = _strip_supplier(p["name"])
        clean_desc = _strip_supplier(p["description"].replace(src_sku, "")) if p["description"] else ""
        prod = SupplierProduct(
            supplier_name="Golyan", supplier_key=SUPPLIER_KEY, supplier_product_id=src_sku,
            supplier_url="", sku=sku, name=clean_name, original_description=clean_desc,
            price=p["price"], stock_status="instock" if available else "outofstock",
            is_available=available, supplier_category=", ".join(p["source_cats"]),
            mapped_category=p["_target"], images=p["images"],
            status="publish" if available else "draft",
        )
        prod.calculated_price = p["_price"]
        # OpenAI rewrite (adds best-price / physical-store / phone footer)
        if content_gen.available:
            try:
                content_gen.enrich(prod)
            except Exception as exc:
                print(f"   enrich fail [{sku}]: {str(exc)[:60]}", flush=True)
        # scrub any supplier mention the model may have echoed; force name/desc
        # to (re)upload on update (ai_generated); SKU stays only in the sku field
        prod.improved_name = _strip_supplier(prod.improved_name) or clean_name
        prod.full_description = _strip_supplier(prod.full_description)
        prod.ai_generated = True
        images_payload = [{"src": u} for u in p["images"]]
        if not images_payload:
            prod.mark_for_review("No image from Golyan"); no_image += 1
        cat_ids = cat_svc.resolve_list(p["_target"])
        ship = shipping_map.get(p["_target"], "")
        try:
            if existing:
                product_svc.update(existing["id"], prod, cat_ids, images_payload, shipping_class=ship)
                updated += 1
            else:
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
