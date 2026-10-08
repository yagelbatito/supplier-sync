# -*- coding: utf-8 -*-
"""Delete ALL Leopard products (SKU prefix 'LPH') from the store.

Owner asked to remove every Leopard item. Default = move to Trash (reversible);
pass --force to delete permanently.

    python delete_leopard.py            # DRY-RUN: count only
    python delete_leopard.py --apply    # to Trash
    python delete_leopard.py --apply --force   # permanent
"""
import sys
import warnings

warnings.filterwarnings("ignore")
from dotenv import load_dotenv

load_dotenv()

from src.woocommerce.client import WooCommerceClient
from src.core.config_loader import load_app_settings

APPLY = "--apply" in sys.argv
FORCE = "--force" in sys.argv


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)

    ids, page = [], 1
    while True:
        b = None
        for _ in range(6):
            b = c.get("products", params={"per_page": 100, "page": page, "status": "any",
                                          "_fields": "id,sku"})
            if b is not None:
                break
        if not b:
            break
        ids += [p["id"] for p in b if str(p.get("sku", "")).startswith("LPH")]
        if len(b) < 100:
            break
        page += 1
        if page % 25 == 0:
            print(f"  scanned {page} pages, LPH so far: {len(ids)}", flush=True)

    print(f"Leopard (LPH) products found: {len(ids)}", flush=True)
    if not APPLY:
        print("(DRY-RUN — nothing deleted.)", flush=True)
        return

    done = 0
    for pid in ids:
        try:
            c.delete(f"products/{pid}", params={"force": FORCE})
            done += 1
        except Exception as exc:
            print(f"  fail {pid}: {str(exc)[:50]}", flush=True)
        if done % 50 == 0:
            print(f"  …{done}/{len(ids)}", flush=True)
    verb = "permanently deleted" if FORCE else "moved to Trash"
    print(f"=== {done} Leopard products {verb} ===", flush=True)


if __name__ == "__main__":
    main()
