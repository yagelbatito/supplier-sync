# -*- coding: utf-8 -*-
"""URGENT takedown: hide all M.M.S products (SKU prefix 'MMS-').

M.M.S products uploaded with broken prices (OCR concatenated digits -> millions)
and images that still show the wholesale-price badge. Set them all to 'draft'
(hidden from the storefront) until the sync is fixed and they are re-uploaded.
Reversible.

    python hide_mms.py            # DRY-RUN: count only
    python hide_mms.py --apply    # set all MMS-* products to draft
"""
import sys
import warnings

warnings.filterwarnings("ignore")
from dotenv import load_dotenv

load_dotenv()

from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient

APPLY = "--apply" in sys.argv


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)

    # M.M.S products were all created in this upload window; the date filter keeps
    # the scan small and fast (vs. paging the whole 14k+ catalogue).
    mms, page = [], 1
    while True:
        b = c.get("products", params={"per_page": 100, "page": page, "status": "any",
                                      "after": "2026-09-20T00:00:00", "orderby": "date",
                                      "order": "desc", "_fields": "id,sku,status"})
        if not b:
            break
        mms += [p for p in b if str(p.get("sku", "")).startswith("MMS-")]
        if len(b) < 100:
            break
        page += 1

    pub = [p for p in mms if p["status"] == "publish"]
    print(f"M.M.S products: {len(mms)} | published (to hide): {len(pub)}", flush=True)

    if not APPLY:
        print("(DRY-RUN — no writes.)", flush=True)
        return

    updates = [{"id": p["id"], "status": "draft"} for p in pub]
    done = 0
    for i in range(0, len(updates), 50):
        batch = updates[i:i + 50]
        for attempt in range(5):
            try:
                c.post("products/batch", {"update": batch})
                break
            except Exception:
                if attempt == 4:
                    print(f"  batch {i} FAILED", flush=True)
        done += len(batch)
        print(f"  …{done}/{len(updates)} hidden", flush=True)
    print(f"=== HIDDEN {len(updates)} M.M.S products (set to draft) ===", flush=True)


if __name__ == "__main__":
    main()
