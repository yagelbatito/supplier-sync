"""Check if SKU exists in trash."""
import sys, warnings
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

# Trash items
print(f"--- Checking trash for SKU={sku} ---")
trashed = client.get("products", params={"sku": sku, "status": "trash", "per_page": 5})
print(f"Trash matches: {len(trashed)}")
for p in trashed:
    print(f"  id={p['id']}  name={p.get('name')!r}  sku={p.get('sku')!r}")

# Try listing ALL trash
print("\n--- All trashed products (page 1) ---")
all_trash = client.get("products", params={"status": "trash", "per_page": 100})
print(f"First page of trash: {len(all_trash)} items")
flo_in_trash = [p for p in all_trash if "FLO-FLORALIS" in (p.get("sku") or "")]
print(f"Floralis SKUs in this page: {len(flo_in_trash)}")
for p in flo_in_trash[:5]:
    print(f"  id={p['id']}  sku={p.get('sku')!r}  name={p.get('name')[:50]!r}")
