# -*- coding: utf-8 -*-
"""Tag the 33 Belini chairs as a reusable collection so the mail-campaign tool can
target them in one click (no pasting 33 links). Creates product tag 'כיסאות בליני'
and assigns it to every BEL-* product, via a single products/batch update.

    python belini_tag.py            # DRY-RUN
    python belini_tag.py --apply
"""
import sys
import warnings

warnings.filterwarnings("ignore")
from dotenv import load_dotenv

load_dotenv()

from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient

TAG = "כיסאות בליני"
SRC_IDS = [6045, 6027, 6022, 5937, 5914, 5755, 5741, 5653, 5647, 5638, 5630, 5540,
           5487, 5473, 5465, 5449, 5410, 5391, 4351, 4315, 4289, 4263, 4224, 4199,
           4185, 4173, 4131, 4125, 4073, 4061, 4038, 4032, 3640]
APPLY = "--apply" in sys.argv


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)

    # ensure tag exists
    found = c.get("products/tags", params={"search": TAG, "_fields": "id,name"})
    tag_id = next((t["id"] for t in (found or []) if t["name"] == TAG), None)
    if tag_id is None:
        if APPLY:
            tag_id = c.post("products/tags", {"name": TAG}).get("id")
        print(f"tag '{TAG}' created -> id {tag_id}", flush=True)
    else:
        print(f"tag '{TAG}' exists -> id {tag_id}", flush=True)

    # resolve store ids by SKU
    store_ids = []
    for sid in SRC_IDS:
        sku = f"BEL-{sid}"
        r = c.get("products", params={"sku": sku, "_fields": "id"})
        if r:
            store_ids.append(r[0]["id"])
    print(f"resolved {len(store_ids)}/{len(SRC_IDS)} products on store", flush=True)

    if not APPLY:
        print("(DRY-RUN — nothing changed.)", flush=True)
        return

    # batch update tags (append; these products had no tags)
    for i in range(0, len(store_ids), 50):
        chunk = store_ids[i:i + 50]
        c.post("products/batch", {"update": [{"id": pid, "tags": [{"id": tag_id}]} for pid in chunk]})
    print(f"=== tagged {len(store_ids)} Belini chairs as '{TAG}' (tag id {tag_id}) ===", flush=True)


if __name__ == "__main__":
    main()
