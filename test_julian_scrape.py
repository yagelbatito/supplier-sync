"""Standalone test — run only the Julian scraper, no WC writes."""
import warnings
warnings.filterwarnings("ignore")

from src.core.config_loader import load_suppliers, load_app_settings
from src.scraping.http_client import HttpClient
from src.suppliers.julian_supplier import JulianSupplier

settings = load_app_settings()
suppliers = load_suppliers()
cfg = suppliers["julian"]

http = HttpClient(verify_ssl=settings.verify_ssl, request_delay=settings.request_delay)
sup = JulianSupplier(cfg, http)

products = sup.scrape()
print(f"\n=== Scraped {len(products)} products ===\n")

# Distribution by supplier_category
from collections import Counter
cats = Counter(p.supplier_category for p in products)
print("Top 30 categories by product count:")
for c, n in cats.most_common(30):
    print(f"  {n:>5}  {c!r}")

# Show 5 sample products
print("\nFirst 5 products:")
for p in products[:5]:
    print(f"  • {p.name!r}")
    print(f"    sku={p.sku}  price={p.price}  stock={p.stock_status}")
    print(f"    category={p.supplier_category!r}")
    print(f"    images={p.images[:1]}{'...' if len(p.images) > 1 else ''}")
    print()
