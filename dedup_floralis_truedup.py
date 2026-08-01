# -*- coding: utf-8 -*-
"""
Merge only TRUE duplicate Floralis products: same NAME **and** same price.
Different-price same-name items are real variants (size/model) → kept.
For each (name, price) group with >1 product, keep the NEWEST id, trash the
rest (force=false → reversible). Dry-run unless --apply.
"""
import sys, io, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
import warnings; warnings.filterwarnings("ignore")
from dotenv import load_dotenv; load_dotenv()
from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient

APPLY = "--apply" in sys.argv
s = load_app_settings()
c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                      consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                      wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=False)

print(f"=== DEDUP FLORALIS true-dups ({'APPLY' if APPLY else 'DRY-RUN'}) ===", flush=True)
allp = c.get_all("products", status="any")  # excludes trash
flo = [p for p in allp if (p.get("sku") or "").upper().startswith("FLO-")]
print(f"active Floralis: {len(flo)}", flush=True)

def price(p):
    return str(p.get("regular_price") or p.get("price") or "")

groups = collections.defaultdict(list)
for p in flo:
    groups[((p.get("name") or "").strip(), price(p))].append(p)

trashed = 0
for (name, pr), ps in groups.items():
    if len(ps) < 2 or not name or not pr:
        continue
    ps.sort(key=lambda x: int(x["id"]))
    keep = ps[-1]                       # newest id
    for old in ps[:-1]:
        print(f"   trash id={old['id']} (keep {keep['id']})  {name[:40]} @ {pr}", flush=True)
        if APPLY:
            try:
                c.delete(f"products/{old['id']}", params={"force": "false"})
            except Exception as e:
                print(f"      fail {old['id']}: {e}", flush=True); continue
        trashed += 1

print(f"\n=== DONE. true-dup copies {'trashed' if APPLY else 'to trash'}={trashed} ({'APPLIED' if APPLY else 'dry-run'}) ===", flush=True)
