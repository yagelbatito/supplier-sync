# -*- coding: utf-8 -*-
"""Remove products whose NAME matches the blocklist (Santa / Christmas).

Finds every product whose name contains a blocked keyword and moves it to the
Trash (reversible). Pair with the ProductService.create() guard, which stops new
ones from being uploaded.

    python remove_blocked_products.py            # DRY-RUN: list matches
    python remove_blocked_products.py --apply    # move them to Trash
    python remove_blocked_products.py --apply --force   # permanently delete
"""
import sys
import warnings

warnings.filterwarnings("ignore")
from dotenv import load_dotenv

load_dotenv()

from src.core.blocklist import BLOCKED_KEYWORDS, is_blocked
from src.woocommerce.client import WooCommerceClient
from src.core.config_loader import load_app_settings

APPLY = "--apply" in sys.argv
FORCE = "--force" in sys.argv


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)

    found = {}
    for kw in BLOCKED_KEYWORDS:
        page = 1
        while True:
            # WC search matches name + description + sku, so a Christmas keyword
            # buried in the description is found too; then confirm with is_blocked
            # over name + both description fields.
            b = c.get("products", params={"per_page": 100, "page": page, "search": kw,
                                          "status": "any",
                                          "_fields": "id,name,sku,description,short_description"})
            if not b:
                break
            for p in b:
                if is_blocked(p.get("name", ""), p.get("description", ""), p.get("short_description", "")):
                    found[p["id"]] = p
            if len(b) < 100:
                break
            page += 1
    print(f"blocked products found (name match): {len(found)}", flush=True)
    for p in sorted(found.values(), key=lambda x: x["id"]):
        print(f"  {p['id']}  {p.get('sku','')}  {p['name'][:55]}", flush=True)

    if not APPLY:
        print("(DRY-RUN — nothing removed.)", flush=True)
        return

    done = 0
    for pid in found:
        try:
            c.delete(f"products/{pid}", params={"force": FORCE})
            done += 1
            if done % 25 == 0:
                print(f"  …{done}/{len(found)} removed", flush=True)
        except Exception as exc:
            print(f"  FAILED {pid}: {str(exc)[:60]}", flush=True)
    verb = "permanently deleted" if FORCE else "moved to Trash"
    print(f"=== {done} products {verb} ===", flush=True)


if __name__ == "__main__":
    main()
