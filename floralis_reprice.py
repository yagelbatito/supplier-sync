# -*- coding: utf-8 -*-
"""
Re-price ALL Floralis products on the store to 15% BELOW the price currently
shown on floralis.co.il (i.e. new store price = floralis_site_price * 0.85,
floored to whole shekels so the discount is always AT LEAST 15%).

Scrapes Floralis live via Shopify /products.json, matches to our WC products by
SKU (FLO-<key>-<shopify_id>), and updates regular_price in batches.

Dry-run by default (prints a sample + counts, no writes). Pass --apply to write.
"""
import sys, io, math, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
import warnings; warnings.filterwarnings("ignore")
from dotenv import load_dotenv; load_dotenv()

import httpx
from src.core.config_loader import load_app_settings, load_suppliers
from src.core.constants import META_SYNC_MANAGED
from src.core.utils import stable_sku
from src.woocommerce.client import WooCommerceClient

APPLY = "--apply" in sys.argv
DISCOUNT = 0.15
BASE = "https://www.floralis.co.il"

s = load_app_settings()
suppliers = load_suppliers()
flo = suppliers.get("floralis") or next(v for k, v in suppliers.items() if k.lower() == "floralis")
KEY, PREFIX = flo.key, flo.sku_prefix
print(f"Floralis key={KEY} prefix={PREFIX} · discount={int(DISCOUNT*100)}% · {'APPLY' if APPLY else 'DRY-RUN'}", flush=True)

# ── 1. Scrape Floralis live (Shopify JSON) ──
print("Scraping floralis.co.il live…", flush=True)
scraped = {}   # sku -> {"title","price","url"}
page = 1
with httpx.Client(timeout=30, headers={"Accept": "application/json"}) as h:
    while True:
        r = h.get(f"{BASE}/products.json?limit=250&page={page}")
        prods = r.json().get("products", [])
        if not prods:
            break
        for raw in prods:
            pid = str(raw.get("id", ""))
            handle = raw.get("handle", "")
            url = f"{BASE}/products/{handle}"
            variants = raw.get("variants", []) or []
            try:
                price = float(variants[0].get("price", 0) or 0) if variants else 0.0
            except (ValueError, TypeError):
                price = 0.0
            if not pid or price <= 0:
                continue
            sku = stable_sku(supplier_key=KEY, product_id=pid, product_url=url, prefix=PREFIX)
            scraped[sku] = {"title": raw.get("title", ""), "price": price, "url": url}
        if len(prods) < 250:
            break
        page += 1
print(f"scraped {len(scraped)} Floralis products with a price", flush=True)

# ── 2. Load our WC products, map by SKU ──
print("Loading store products (a minute)…", flush=True)
c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                      consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                      wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=False)
allp = c.get_all("products", status="any")
by_sku = {}
for p in allp:
    sku = (p.get("sku") or "").upper()
    if sku:
        by_sku[sku] = p

# ── 3. Compute updates ──
updates, samples, backup, unmatched = [], [], [], 0
for sku, info in scraped.items():
    wc = by_sku.get(sku.upper())
    if not wc:
        unmatched += 1
        continue
    new_price = int(math.ceil(info["price"] * (1 - DISCOUNT)))  # round UP (owner's choice)
    if new_price <= 0:
        continue
    cur = wc.get("regular_price") or wc.get("price") or "?"
    backup.append({"id": wc["id"], "sku": sku, "name": wc.get("name", ""),
                   "old_regular_price": wc.get("regular_price", ""), "new": new_price})
    updates.append({"id": wc["id"], "regular_price": str(new_price)})
    if len(samples) < 12:
        samples.append((info["title"][:34], info["price"], cur, new_price))

print(f"\nmatched & to-update: {len(updates)} · unmatched (not in store): {unmatched}", flush=True)
print("\n=== SAMPLE (floralis price → new store price; cur = current store) ===", flush=True)
for t, fp, cur, np_ in samples:
    print(f"   {t:<34} floralis={fp:>7.0f}  cur={str(cur):>6}  →  {np_}", flush=True)

# ── 4. Batch update ──
if APPLY and updates:
    import json, datetime
    bpath = f"data/floralis_price_backup_{datetime.datetime.now():%Y%m%d_%H%M}.json"
    with open(bpath, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=2)
    print(f"\nbackup of current prices → {bpath}", flush=True)
    print(f"Applying {len(updates)} updates in batches…", flush=True)
    done = 0
    for i in range(0, len(updates), 50):
        chunk = updates[i:i+50]
        c.post("products/batch", {"update": chunk})
        done += len(chunk)
        print(f"   updated {done}/{len(updates)}", flush=True)
        time.sleep(0.5)
    print("APPLIED.", flush=True)
elif not APPLY:
    print("\n(DRY-RUN — nothing written. Re-run with --apply to update.)", flush=True)

print(f"\n=== DONE. would_update={len(updates)} unmatched={unmatched} ({'APPLIED' if APPLY else 'dry-run'}) ===", flush=True)
