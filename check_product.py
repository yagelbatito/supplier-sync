"""Check meta_data on a specific WC product by SKU."""
import sys
import warnings
warnings.filterwarnings("ignore")

from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient

settings = load_app_settings()
client = WooCommerceClient(
    url=settings.woocommerce_url,
    consumer_key=settings.woocommerce_key,
    consumer_secret=settings.woocommerce_secret,
    wp_user=settings.wp_user,
    wp_app_password=settings.wp_app_password,
    verify_ssl=settings.verify_ssl,
)

sku = sys.argv[1] if len(sys.argv) > 1 else "FLO-FLORALIS-8603986198665"

# Try multiple variants to debug WC's query behavior
for label, params in [
    ("status=any",        {"sku": sku, "status": "any", "per_page": 1}),
    ("status=draft",      {"sku": sku, "status": "draft", "per_page": 1}),
    ("status=publish",    {"sku": sku, "status": "publish", "per_page": 1}),
    ("no-status filter",  {"sku": sku, "per_page": 1}),
    ("search by SKU",     {"search": sku, "status": "any", "per_page": 5}),
]:
    try:
        results = client.get("products", params=params)
        print(f"[{label}] → {len(results)} hits")
        for r in results:
            print(f"    id={r.get('id')} status={r.get('status')!r} sku={r.get('sku')!r}")
    except Exception as e:
        print(f"[{label}] EXCEPTION: {e}")

# Now also iterate ALL products to find this SKU
print("\nSearching across all 6 pages of products for the SKU...")
all_p = client.get_all("products", status="any")
print(f"Total products on site: {len(all_p)}")
matches = [p for p in all_p if p.get("sku", "").strip() == sku]
print(f"Exact SKU matches: {len(matches)}")
for p in matches:
    print(f"  id={p.get('id')} status={p.get('status')!r} name={p.get('name')!r}")
    print(f"  raw sku bytes: {p.get('sku').encode('utf-8')!r}")

if not matches:
    # Look for partial matches in case of trailing whitespace
    partial = [p for p in all_p if sku in (p.get("sku") or "")]
    print(f"Partial matches (sku contains '{sku}'): {len(partial)}")
    for p in partial[:3]:
        print(f"  id={p.get('id')} sku bytes: {p.get('sku').encode('utf-8')!r}")
sys.exit(0)

if not results:
    print(f"NO PRODUCT found for SKU {sku}")
else:
    p = results[0]
    print(f"ID:     {p.get('id')}")
    print(f"Name:   {p.get('name')}")
    print(f"Status: {p.get('status')}")
    print(f"SKU:    {p.get('sku')}")
    print(f"Cats:   {[c['name'] for c in p.get('categories', [])]}")
    print(f"Images: {len(p.get('images', []))}")
    print("Meta_data:")
    for m in p.get("meta_data", []):
        print(f"  {m['key']!r:40} = {str(m['value'])[:80]!r}")
