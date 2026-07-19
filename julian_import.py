"""
julian_import.py — import Golyan/Julian products into EXISTING WC categories,
with strict name de-duplication.

- Only imports the whitelisted leaf categories (the menu groups the user asked
  for); everything else is skipped for now.
- Maps each Julian leaf category to an existing WC category (JULIAN_CAT_MAP).
- NAME DEDUPE: never creates a product whose name already exists among managed
  products (across all suppliers) or was already created in this run.

Usage:
    python julian_import.py            # DRY-RUN, all whitelisted
    python julian_import.py --limit 2  # DRY-RUN, max 2 per category
    python julian_import.py --limit 2 --apply
    python julian_import.py --apply     # full import (careful)
"""
import sys, collections, warnings
warnings.filterwarnings("ignore")
from src.core.config_loader import (load_app_settings, load_suppliers, load_shipping_class_mapping)
from src.enrichment.openai_client import OpenAIClient
from src.enrichment.product_content_generator import ProductContentGenerator
from src.scraping.http_client import HttpClient
from src.matching.category_matcher import CategoryMatcher
from src.suppliers.julian_supplier import JulianSupplier
from src.woocommerce.client import WooCommerceClient
from src.woocommerce.category_service import CategoryService
from src.woocommerce.media_service import MediaService
from src.woocommerce.product_service import ProductService
from src.core.constants import META_SYNC_MANAGED
from run_and_verify import process_product

# Julian leaf category -> existing WC category
JULIAN_CAT_MAP = {
    # ריהוט משלים
    "מראות": "מראות", "ספריות שידות ומזנונים": "מזנונים", "שולחנות": "שולחנות",
    "כסאות כורסאות וספות": "ריהוט", "עגלות תה ומטבח": "מטבח ואירוח",
    # אקססוריז
    "אגרטלים": "אקססוריז", "ואזות": "אקססוריז", "תאורה משלימה": "תאורה",
    "פרחים": "צמחים", "עציצים": "צמחים", "עצים": "צמחים", "צמחיה מלאכותית": "צמחים",
    "נרות אוירה וריח": "נרות", "פמוטים ומתקני נר": "נרות", "שעונים": "שעונים",
    "פסלי נוי": "אקססוריז", "אביזרי אמבטיה": "אביזרי אמבטיה", "פחים": "אקססוריז",
    "סלסלות נוי": "אקססוריז", "אביזרי תליה": "אקססוריז", "מאפרות": "אקססוריז",
    "אביזרים משלימים": "אקססוריז",
    # יודאיקה
    "מגשי שבת": "יודאיקה", "קידוש והבדלה": "יודאיקה", "פמוטי שבת": "יודאיקה",
    "נטלות": "נטלות", "מלחיות יהודיות": "יודאיקה", "יודאיקה לבית": "יודאיקה",
    "יודאיקה טקסטיל": "יודאיקה", "ראש השנה": "יודאיקה", "חנוכה": "חנוכה", "פסח": "יודאיקה",
}
WHITELIST = set(JULIAN_CAT_MAP)

apply = "--apply" in sys.argv
limit = 0
if "--limit" in sys.argv:
    limit = int(sys.argv[sys.argv.index("--limit") + 1])

s = load_app_settings()
wc = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                       consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                       wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl,
                       dry_run=not apply)
product_svc = ProductService(wc); media_svc = MediaService(wc, verify_ssl=s.verify_ssl)
category_svc = CategoryService(wc); category_svc.load()
gen = ProductContentGenerator(OpenAIClient(api_key=s.openai_api_key, model=s.openai_model))
shipping = load_shipping_class_mapping()
cfg = load_suppliers()["julian"]
# Matcher whose direct mapping forces each Julian leaf category to the mapped
# WC category (process_product always calls matcher.match).
matcher = CategoryMatcher(category_mapping={"julian": JULIAN_CAT_MAP},
                          keyword_rules={}, wc_categories=category_svc.category_names)

def norm(n): return " ".join((n or "").split()).strip().lower()

print("Building NAME index of existing managed products (dedupe)...")
existing_names = set()
warmed = 0
for p in wc.get_all("products", status="any"):
    meta = {m["key"]: m["value"] for m in p.get("meta_data", [])}
    if str(meta.get(META_SYNC_MANAGED, "")).lower() in ("true", "1"):
        existing_names.add(norm(p.get("name", "")))
    sku = p.get("sku")
    if sku:
        product_svc._sku_cache[sku] = p; warmed += 1
print(f"  existing managed names: {len(existing_names)} | sku cache: {warmed}")

print("Scraping Julian...")
julian = JulianSupplier(cfg, HttpClient(verify_ssl=s.verify_ssl, request_delay=0.3))
all_products = julian.run()
print(f"  scraped {len(all_products)} products")

# filter to whitelist + apply per-category limit
by_cat = collections.defaultdict(list)
for p in all_products:
    if p.supplier_category in WHITELIST:
        by_cat[p.supplier_category].append(p)

created = dup = skipped_cat = failed = 0
seen_this_run = set()
stats = collections.defaultdict(int)
for cat, prods in by_cat.items():
    take = prods[:limit] if limit else prods
    wc_cat = JULIAN_CAT_MAP[cat]
    print(f"\n=== {cat} -> {wc_cat}  ({len(take)}/{len(prods)}) ===")
    for p in take:
        nn = norm(p.name)
        # Skip only if this name belongs to a DIFFERENT product. If this exact
        # Julian item already exists (same SKU), let it through so process_product
        # UPDATES its stock/price — that's what makes re-runs (weekly sync) safe.
        sku_exists = p.sku in product_svc._sku_cache
        if (nn in existing_names or nn in seen_this_run) and not sku_exists:
            dup += 1
            print(f"  ⏭️  DUP name, skip: {p.name[:40]}")
            continue
        if not apply:
            print(f"  [dry] would create: {p.name[:40]} | {p.calculated_price}₪ -> {wc_cat}")
            created += 1; seen_this_run.add(nn); continue
        try:
            action, info = process_product(p, cfg, product_svc, media_svc, category_svc,
                                           matcher, gen, shipping, stats, supplier=julian)
            if action in ("ok_created", "ok_restored", "ok_updated"):
                created += 1; seen_this_run.add(nn)
                print(f"  ✅ {action}: {p.name[:35]} -> {[c for c in info.get('category',[])]}")
            else:
                failed += 1
                print(f"  ⚠️ {action}: {p.name[:35]}")
        except Exception as e:
            failed += 1
            print(f"  ❌ FAIL {p.name[:30]}: {str(e)[:50]}")

print(f"\n{'APPLIED' if apply else 'DRY-RUN'}. created={created} dup-skipped={dup} failed={failed}")
