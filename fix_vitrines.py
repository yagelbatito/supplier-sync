# -*- coding: utf-8 -*-
"""Move vitrines/shelves from מזנונים → ספריות. Catches the construct form
"ויטרינת" (not just "ויטרינה") and shelf units (מדף/מדפים) that the first
sweep missed. Dry-run unless --apply."""
import sys, io, json, collections
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
SRC = cat_svc.resolve("מזנונים", fallback="")
DST = cat_svc.resolve("ספריות", fallback="")

def is_shelf(n):
    return (("ויטרינה" in n) or ("ויטרינת" in n) or ("ויטרינות" in n)
            or ("מדף" in n) or ("מדפים" in n))

print(f"=== FIX VITRINES ({'APPLY' if APPLY else 'DRY-RUN'}) · מזנונים({SRC})→ספריות({DST}) ===", flush=True)
prods = c.get_all("products", category=str(SRC), status="any")
moved = []
for p in prods:
    nm = (p.get("name") or "").strip()
    if is_shelf(nm):
        moved.append(nm)
        print(f"   {nm[:50]}", flush=True)
        if APPLY:
            c.put(f"products/{p['id']}", {"categories": [{"id": DST}]})
with open("data/vitrines_moved.json", "w", encoding="utf-8") as f:
    json.dump(moved, f, ensure_ascii=False, indent=2)
print(f"\n=== {'moved' if APPLY else 'to move'}={len(moved)} of {len(prods)} in מזנונים ===", flush=True)
