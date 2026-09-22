# -*- coding: utf-8 -*-
"""Hide ONLY the M.M.S products with a broken/exorbitant price.

The image price-OCR sometimes glued digits together, producing absurd prices
(millions). Those specific products are wrong and must be hidden; the rest of
the M.M.S catalogue is correctly priced and stays live. Threshold: any M.M.S
product priced above PRICE_MAX is treated as broken. Reversible (draft).

    python hide_mms.py            # DRY-RUN: list what would be hidden
    python hide_mms.py --apply
"""
import sys
import warnings

warnings.filterwarnings("ignore")
from dotenv import load_dotenv

load_dotenv()

from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient

APPLY = "--apply" in sys.argv
PRICE_MAX = 10000        # legit M.M.S furniture tops out ~₪4,250; broken ones are >₪100,000


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)

    mms, page = [], 1
    while True:
        b = c.get("products", params={"per_page": 100, "page": page, "status": "any",
                                      "after": "2026-09-20T00:00:00", "orderby": "date",
                                      "order": "desc", "_fields": "id,sku,status,price,name"})
        if not b:
            break
        mms += [p for p in b if str(p.get("sku", "")).startswith("MMS-")]
        if len(b) < 100:
            break
        page += 1

    def _p(p):
        try:
            return float(p.get("price") or 0)
        except ValueError:
            return 0.0

    broken = [p for p in mms if _p(p) > PRICE_MAX and p["status"] == "publish"]
    print(f"M.M.S total={len(mms)} | broken-price & published (to hide): {len(broken)}", flush=True)
    for p in broken:
        print(f"  {_p(p):>14,.0f}  {p['sku']:<14} {p['name'][:40]}", flush=True)

    if not APPLY:
        print("(DRY-RUN — no writes.)", flush=True)
        return

    updates = [{"id": p["id"], "status": "draft"} for p in broken]
    for i in range(0, len(updates), 50):
        c.post("products/batch", {"update": updates[i:i + 50]})
    print(f"=== HIDDEN {len(updates)} broken-price M.M.S products ===", flush=True)


if __name__ == "__main__":
    main()
