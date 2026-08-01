# -*- coding: utf-8 -*-
"""In the 'סט צלחות' category, move כוס/כוסות/סט כוסות → 'כוסות'.
Dry-run unless --apply."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
import warnings; warnings.filterwarnings("ignore")
from dotenv import load_dotenv; load_dotenv()
from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient
from src.woocommerce.category_service import CategoryService

APPLY = "--apply" in sys.argv
s = load_app_settings()
c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                      consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                      wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=False)
cat_svc = CategoryService(c); cat_svc.load()
SRC = cat_svc.resolve("סט צלחות", fallback="")
DST = cat_svc.resolve("כוסות", fallback="")
print(f"=== CUPS SWEEP ({'APPLY' if APPLY else 'DRY-RUN'}) · סט צלחות({SRC})→כוסות({DST}) ===", flush=True)
prods = c.get_all("products", category=str(SRC), status="any")
moved = []
for p in prods:
    nm = (p.get("name") or "").strip()
    if ("כוסות" in nm) or ("כוס " in nm) or nm.startswith("כוס") or ("סט כוסות" in nm):
        moved.append(nm)
        print(f"   {nm[:50]}", flush=True)
        if APPLY:
            c.put(f"products/{p['id']}", {"categories": [{"id": DST}]})
json.dump(moved, open("data/cups_moved.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"\n=== {'moved' if APPLY else 'to move'}={len(moved)} of {len(prods)} in סט צלחות ===", flush=True)
