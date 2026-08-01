# -*- coding: utf-8 -*-
"""
  • In 'כוסות': name has תה or קפה → 'סטים תה קפה סוכר'
  • In 'שטיחים': name has עץ         → 'צמחים'
Dry-run unless --apply. Dumps moved names to data/rugs_cups_moved.json.
"""
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
moved = collections.defaultdict(list)


def sweep(src_name, dst_name, match):
    src = cat_svc.resolve(src_name, fallback="")
    dst = cat_svc.resolve(dst_name, fallback="")
    prods = c.get_all("products", category=str(src), status="any")
    n = 0
    print(f"\n### {src_name}({src}) → {dst_name}({dst}): {len(prods)} products", flush=True)
    for p in prods:
        nm = (p.get("name") or "").strip()
        if match(nm):
            n += 1; moved[dst_name].append(nm)
            print(f"   {nm[:48]}", flush=True)
            if APPLY:
                c.put(f"products/{p['id']}", {"categories": [{"id": dst}]})
    print(f"   → {n} moved", flush=True)
    return n


print(f"=== RUGS/CUPS SWEEP ({'APPLY' if APPLY else 'DRY-RUN'}) ===", flush=True)
a = sweep("כוסות", "סטים תה קפה סוכר", lambda n: ("תה" in n) or ("קפה" in n))
# The שטיחים→צמחים rule is gated behind --rugs: the matches are actual rugs
# named after trees ("שטיח עץ זית"), so confirm before moving.
b = sweep("שטיחים", "צמחים", lambda n: "עץ" in n) if "--rugs" in sys.argv else 0
json.dump(moved, open("data/rugs_cups_moved.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"\n=== total={a + b} ({'APPLIED' if APPLY else 'dry-run'}) ===", flush=True)
