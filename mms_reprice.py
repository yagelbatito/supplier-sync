# -*- coding: utf-8 -*-
"""Re-price all existing M.M.S (Miki Shaham) products to banner price x<mult>.

Recomputes each price EXACTLY from the stored source banner price
(meta `original_supplier_price`) x the multiplier — not by scaling the current
price — so there is no compounding rounding. Matches the sync's own rule:
ceil(banner * mult). Updates regular_price + the calculated-price/last-update
meta. Stock, content and images are untouched.

Used both for one-off re-pricing and by the daily alternating-price job
(mms-daily-price.yml), which passes --mult=1.80 or 1.85 depending on the day.

    python mms_reprice.py --mult=1.85             # DRY-RUN (report changes)
    python mms_reprice.py --mult=1.80 --apply
"""
import math
import sys
import warnings

warnings.filterwarnings("ignore")
from dotenv import load_dotenv

load_dotenv()

from src.core.config_loader import load_app_settings
from src.core.constants import (META_CALCULATED_PRICE, META_LAST_PRICE_UPDATE,
                                 META_ORIGINAL_PRICE, META_SUPPLIER_NAME)
from src.core.utils import now_iso
from src.woocommerce.client import WooCommerceClient

SUPPLIER = "M.M.S"
APPLY = "--apply" in sys.argv
NEW_MULT = next((float(a.split("=")[1]) for a in sys.argv if a.startswith("--mult=")), 1.8)


def meta_get(p, key):
    for m in p.get("meta_data", []):
        if m.get("key") == key:
            return m.get("value")
    return None


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)

    stats = {"scanned": 0, "mms": 0, "repriced": 0, "unchanged": 0, "no_source": 0, "failed": 0}
    page = 1
    while True:
        batch = None
        for _ in range(6):
            batch = c.get("products", params={"per_page": 100, "page": page, "status": "any",
                                              "_fields": "id,sku,regular_price,meta_data"})
            if batch is not None:
                break
        if not batch:
            break
        for p in batch:
            stats["scanned"] += 1
            sku = p.get("sku", "") or ""
            is_mms = (meta_get(p, META_SUPPLIER_NAME) == SUPPLIER) or sku.startswith("MMS-")
            if not is_mms:
                continue
            stats["mms"] += 1
            orig = meta_get(p, META_ORIGINAL_PRICE)
            try:
                orig_v = float(orig)
            except (TypeError, ValueError):
                orig_v = 0
            if not orig_v:
                stats["no_source"] += 1
                print(f"  ⚠ no source price, skipped: {sku}", flush=True)
                continue
            new_price = int(math.ceil(orig_v * NEW_MULT))
            cur = p.get("regular_price")
            try:
                cur_v = int(round(float(cur)))
            except (TypeError, ValueError):
                cur_v = None
            if cur_v == new_price:
                stats["unchanged"] += 1
                continue
            if APPLY:
                try:
                    c.put(f"products/{p['id']}", {
                        "regular_price": str(new_price),
                        "meta_data": [
                            {"key": META_CALCULATED_PRICE, "value": str(new_price)},
                            {"key": META_LAST_PRICE_UPDATE, "value": now_iso()},
                        ],
                    })
                except Exception as exc:
                    stats["failed"] += 1
                    print(f"  FAIL {sku}: {str(exc)[:70]}", flush=True)
                    continue
            stats["repriced"] += 1
            if stats["repriced"] <= 15 or stats["repriced"] % 50 == 0:
                print(f"  {sku}: banner {orig_v:.0f} | {cur} → {new_price}", flush=True)
        if len(batch) < 100:
            break
        page += 1

    print(f"\n=== M.M.S reprice x{NEW_MULT} {'APPLIED' if APPLY else 'DRY-RUN'} ===", flush=True)
    print(stats, flush=True)


if __name__ == "__main__":
    main()
