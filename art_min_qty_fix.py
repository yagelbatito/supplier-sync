# -*- coding: utf-8 -*-
"""Update ONLY the _min_order_qty meta on ART products that have a supplier
pack/minimum > 1 — set to AT LEAST 50% (rounded up). Does NOT re-enrich, re-image
or re-create anything; it just fetches the supplier's pack sizes and patches the
minimum on the ~350 relevant products.

    python art_min_qty_fix.py            # DRY-RUN
    python art_min_qty_fix.py --apply
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
from dotenv import load_dotenv

load_dotenv()

from src.core.config_loader import load_app_settings
from src.core.utils import stable_sku
from src.suppliers import israel_judaica_supplier as ij
from src.woocommerce.client import WooCommerceClient

APPLY = "--apply" in sys.argv
SUPPLIER_KEY = "art"
SKU_PREFIX = "ART"


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)

    session = ij.login(os.getenv("IJ_USER", ""), os.getenv("IJ_PASSWORD", ""))
    if not session:
        print("!! ART login failed — check IJ_USER / IJ_PASSWORD", flush=True)
        sys.exit(1)
    products = ij.fetch_all(session)
    print(f"fetched {len(products)} supplier products", flush=True)

    targets = []
    for p in products:
        mo = int(p.get("min_order", 0) or 0)
        if mo > 1:
            store_sku = stable_sku(supplier_key=SUPPLIER_KEY, product_id=p["sku"],
                                   product_url="", prefix=SKU_PREFIX)
            targets.append((store_sku, mo, max(1, (mo + 1) // 2)))
    print(f"supplier products with pack>1: {len(targets)}", flush=True)

    updates, missing = [], 0
    for store_sku, mo, qty in targets:
        try:
            r = c.get("products", params={"sku": store_sku, "_fields": "id"})
        except Exception:
            r = None
        if r:
            updates.append({"id": r[0]["id"],
                            "meta_data": [{"key": "_min_order_qty", "value": str(qty)}]})
        else:
            missing += 1
    print(f"matched on store: {len(updates)} | not found: {missing}", flush=True)
    # sample
    for store_sku, mo, qty in targets[:10]:
        print(f"   pack {mo} -> min {qty}   {store_sku}", flush=True)

    if not APPLY:
        print("(DRY-RUN — no writes.)", flush=True)
        return
    done = 0
    for i in range(0, len(updates), 50):
        c.post("products/batch", {"update": updates[i:i + 50]})
        done += len(updates[i:i + 50])
        print(f"  …{done}/{len(updates)} updated", flush=True)
    print(f"=== UPDATED min-order on {len(updates)} ART products (>=50%) ===", flush=True)


if __name__ == "__main__":
    main()
