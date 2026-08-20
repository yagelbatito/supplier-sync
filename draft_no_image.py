# -*- coding: utf-8 -*-
"""Safety net: any PUBLISHED product with NO image → set to draft, so nothing
is ever live without a picture. Dry-run unless --apply."""
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

def has_image(p):
    return any((im.get("id") or im.get("src")) for im in (p.get("images") or []))

print(f"=== DRAFT NO-IMAGE ({'APPLY' if APPLY else 'DRY-RUN'}) ===", flush=True)
allp = c.get_all("products", status="publish")
bad = [p for p in allp if not has_image(p)]
print(f"published: {len(allp)} | published WITHOUT image: {len(bad)}", flush=True)

by_sku_prefix = collections.Counter()
for p in bad:
    sku = (p.get("sku") or "")
    pre = sku.split("-")[0] if "-" in sku else (sku[:6] or "—")
    by_sku_prefix[pre] += 1
print("by SKU prefix:", dict(by_sku_prefix.most_common()), flush=True)
for p in bad[:25]:
    print(f"   id={p['id']} sku={p.get('sku','')}  {p.get('name','')[:40]}", flush=True)

if APPLY and bad:
    done = 0
    for i in range(0, len(bad), 50):
        chunk = [{"id": p["id"], "status": "draft"} for p in bad[i:i+50]]
        c.post("products/batch", {"update": chunk})
        done += len(chunk)
        print(f"   drafted {done}/{len(bad)}", flush=True)
    print("APPLIED.", flush=True)
print(f"=== DONE. no-image published={len(bad)} ({'DRAFTED' if APPLY else 'dry-run'}) ===", flush=True)
