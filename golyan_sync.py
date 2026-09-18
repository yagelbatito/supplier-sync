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
import sys, io, math, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
import warnings; warnings.filterwarnings("ignore")
from dotenv import load_dotenv; load_dotenv()

from src.core.config_loader import load_app_settings, load_shipping_class_mapping
from src.core.constants import META_SYNC_MANAGED
from src.core.utils import stable_sku
from src.models.product import SupplierProduct
from src.woocommerce.client import WooCommerceClient
from src.woocommerce.category_service import CategoryService
from src.woocommerce.product_service import ProductService
from src.suppliers.golyan_supplier import fetch_all
from config.golyan_mapping import map_product, FURNITURE_TARGETS, NEW_CATEGORIES

SUPPLIER_KEY = "julian"     # keep continuity with existing JUL-* products
SKU_PREFIX = "JUL"


def _norm(n: str) -> str:
    return " ".join((n or "").split()).strip().lower()

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

    # ── 6-8. map + price ──
    dist = collections.Counter()
    unmapped = []
    missing_target = collections.defaultdict(list)   # target -> [(sku,name)]
    price_samples = []
    for sku, p in by_sku.items():
        res = map_product(p["name"], p["source_cats"])
        if not res.target:
            unmapped.append(p); continue
        dist[res.target] += 1
        # target category must exist (or be one we will create)
        if res.target not in store_cats and res.target not in new_leaves:
            missing_target[res.target].append((sku, p["name"]))
        p["_target"] = res.target
        p["_furn"] = res.is_furniture
        p["_price"] = price_for(p["price"], res.is_furniture)
        if len(price_samples) < 10:
            price_samples.append((p["name"][:30], p["price"], p["_price"],
                                  "ריהוט −30%" if res.is_furniture else "אחר −25%"))

    # ═══════════════ REPORTS ═══════════════
    print("\n── mapping distribution (target → count) ──", flush=True)
    for t, n in dist.most_common():
        flag = " [ריהוט]" if t in FURNITURE_TARGETS else ""
        exists = "" if (t in store_cats or t in new_leaves) else "  ⚠️ קטגוריה לא קיימת בחנות"
        print(f"  {n:4}  {t}{flag}{exists}", flush=True)

    print("\n── price samples (source → store) ──", flush=True)
    for name, src, new, tag in price_samples:
        print(f"  {name:<30} {src:>7.0f} → {new:>6}  ({tag})", flush=True)

    print(f"\n── UNMAPPED_PRODUCTS: {len(unmapped)} ──", flush=True)
    for p in unmapped[:25]:
        print(f"  [{p['sku']}] {p['name'][:40]}  « {', '.join(p['source_cats'][:2])}", flush=True)
    if len(unmapped) > 25:
        print(f"  … +{len(unmapped) - 25} more", flush=True)

    print(f"\n── HIDDEN_OR_MISSING_FRONTEND_CATEGORIES: {len(missing_target)} ──", flush=True)
    for t, items in missing_target.items():
        will = " (מיועד ליצירה)" if t in new_leaves else " (לא ברשימת היצירה — דיווח בלבד)"
        print(f"  '{t}': {len(items)} מוצרים{will}  דוגמאות: {[s for s,_ in items[:3]]}", flush=True)

    if dup_source:
        print(f"\n── duplicate source SKUs: {len(dup_source)} ──", flush=True)
        for sku, n in list(dup_source.items())[:10]:
            print(f"  {sku} ×{n+1}", flush=True)

    print(f"\n=== SUMMARY ===", flush=True)
    print(f"  fetched={len(products)} unique={len(by_sku)} mapped={sum(dist.values())} "
          f"unmapped={len(unmapped)} dup-source={len(dup_source)} "
          f"missing-cats={len(missing_target)} ({'APPLIED' if APPLY else 'DRY-RUN'})", flush=True)

    if not APPLY:
        print("\n(DRY-RUN — no writes. Review the reports above, then run --apply.)", flush=True)
        return

    # ═══════════════ APPLY ═══════════════
    product_svc = ProductService(c)
    shipping_map = load_shipping_class_mapping()

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
    for p in c.get_all("products", status="any"):
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
        prod = SupplierProduct(
            supplier_name="Golyan", supplier_key=SUPPLIER_KEY, supplier_product_id=src_sku,
            supplier_url="", sku=sku, name=p["name"], original_description=p["description"],
            price=p["price"], stock_status="instock" if available else "outofstock",
            is_available=available, supplier_category=", ".join(p["source_cats"]),
            mapped_category=p["_target"], images=p["images"],
            status="publish" if available else "draft",
        )
        prod.calculated_price = p["_price"]
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
