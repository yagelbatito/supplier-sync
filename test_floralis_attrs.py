"""Quick test — verify accordion extraction on one Floralis product."""
import warnings
warnings.filterwarnings("ignore")

from src.core.config_loader import load_suppliers, load_app_settings
from src.scraping.http_client import HttpClient
from src.suppliers.floralis_supplier import FloralisSupplier

settings = load_app_settings()
suppliers = load_suppliers()
cfg = suppliers["floralis"]
http = HttpClient(verify_ssl=settings.verify_ssl, request_delay=settings.request_delay)

sup = FloralisSupplier(cfg, http)

# Probe the raw page-attrs helper on the known console product
url = "https://www.floralis.co.il/products/קונסולה-לוסט-וולנאט-m-1"
print(f"Fetching attrs from: {url}\n")
attrs = sup._fetch_page_attrs(url)
for label, content in attrs.items():
    print(f"  [{label}]")
    print(f"    → {content[:200]}")
    print()

# Now exercise the full parser on a single JSON product
print("=" * 60)
print("Full _parse_json_product test (first product from /products.json)")
print("=" * 60)
import json, urllib.request, ssl
ctx = ssl._create_unverified_context()
raw = urllib.request.urlopen("https://www.floralis.co.il/products.json?limit=3", context=ctx, timeout=20).read()
data = json.loads(raw)
for raw_p in data["products"][:2]:
    p = sup._parse_json_product(raw_p)
    if p:
        print(f"\nproduct: {p.name!r}")
        print(f"  category:    {p.supplier_category!r}")
        print(f"  price:       {p.price}")
        print(f"  images:      {len(p.images)}")
        print(f"  material:    {p.material!r}")
        print(f"  color:       {p.color!r}")
        print(f"  width:       {p.width!r}")
        print(f"  height:      {p.height!r}")
        print(f"  depth:       {p.depth!r}")
        print(f"  dimensions:  {p.dimensions!r}")
        print(f"  description: {p.original_description[:200]!r}")
