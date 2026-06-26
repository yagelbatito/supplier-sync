"""Ad-hoc helper to list WooCommerce categories, reusing the project's client."""
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
cats = client.get_all("products/categories")
print(f"Total: {len(cats)}")
for c in sorted(cats, key=lambda x: x.get("name", "")):
    print(f'  {c["id"]:>5}  parent={c.get("parent", 0):>5}  {c["name"]!r}')
