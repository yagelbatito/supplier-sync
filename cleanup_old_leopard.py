"""
cleanup_old_leopard.py — after the SKU fix + Leopard re-import, the old
collided records remain (their SKUs are in the OLD truncated format, without
the new '-<10 hex>' URL-hash suffix). This trashes them.

Usage:
    python cleanup_old_leopard.py            # dry-run (list only)
    python cleanup_old_leopard.py --apply    # move old records to trash
"""
import re
import sys
import warnings

warnings.filterwarnings("ignore")

import requests

from src.core.config_loader import load_app_settings
from src.core.constants import META_SYNC_MANAGED, META_SUPPLIER_NAME
from src.woocommerce.client import WooCommerceClient

# New SKUs end with a 10-hex URL hash (e.g. ...-E651481D5F). Old collided ones
# do not.
NEW_SKU = re.compile(r"-[0-9A-F]{10}$", re.I)


def main():
    apply = "--apply" in sys.argv
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl,
                          dry_run=False)

    allp = c.get_all("products", status="any")
    old, new = [], 0
    for p in allp:
        meta = {m["key"]: m["value"] for m in p.get("meta_data", [])}
        if str(meta.get(META_SYNC_MANAGED, "")).lower() not in ("true", "1"):
            continue
        if meta.get(META_SUPPLIER_NAME, "").lower() != "leopard":
            continue
        sku = p.get("sku") or ""
        if NEW_SKU.search(sku):
            new += 1
        else:
            old.append((p["id"], sku, (p.get("name") or "")[:32], p.get("status")))

    print(f"Leopard managed: {new} new-format (keep), {len(old)} old-format (trash)")
    for pid, sku, nm, st in old:
        print(f"   {pid}  [{st}]  {sku[:42]}  {nm}")

    if not apply:
        print("\n(DRY-RUN — re-run with --apply to move these to trash)")
        return 0

    trashed = 0
    for pid, sku, nm, st in old:
        url = f"{c.api_base}/products/{pid}"
        try:
            r = c._session.delete(url, params={"consumer_key": s.woocommerce_key,
                                               "consumer_secret": s.woocommerce_secret,
                                               "force": "false"},
                                  verify=s.verify_ssl, timeout=60)
            if r.status_code in (200, 201):
                trashed += 1
            else:
                print(f"   ✗ {pid}: {r.status_code} {r.text[:120]}")
        except Exception as exc:
            print(f"   ✗ {pid}: {exc}")
    print(f"\nTrashed {trashed}/{len(old)} old Leopard records.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
