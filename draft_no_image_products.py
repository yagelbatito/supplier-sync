# -*- coding: utf-8 -*-
"""Move every PUBLISHED product that has no product image to Draft (hidden).

A product without a photo looks broken on the storefront. New ones are already
gated at creation (ProductService: no image -> draft); this cleans up existing
published products that ended up image-less. Reversible.

    python draft_no_image_products.py            # DRY-RUN: count + sample
    python draft_no_image_products.py --apply
"""
import sys
import warnings

warnings.filterwarnings("ignore")
from dotenv import load_dotenv

load_dotenv()

from src.woocommerce.client import WooCommerceClient
from src.core.config_loader import load_app_settings

APPLY = "--apply" in sys.argv


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)

    no_img, page = [], 1
    while True:
        b = c.get("products", params={"per_page": 100, "page": page, "status": "publish",
                                      "_fields": "id,name,sku,images"})
        if not b:
            break
        for p in b:
            if not p.get("images"):
                no_img.append(p)
        if len(b) < 100:
            break
        page += 1
        if page % 25 == 0:
            print(f"  scanned {page} pages, no-image so far: {len(no_img)}", flush=True)

    print(f"published products with NO image: {len(no_img)}", flush=True)
    for p in no_img[:20]:
        print(f"  {p['id']}  {p.get('sku','')}  {p.get('name','')[:45]}", flush=True)
    if len(no_img) > 20:
        print(f"  … +{len(no_img)-20} more", flush=True)

    if not APPLY:
        print("(DRY-RUN — nothing changed.)", flush=True)
        return

    updates = [{"id": p["id"], "status": "draft"} for p in no_img]
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
        print(f"  …{done}/{len(updates)} drafted", flush=True)
    print(f"=== DRAFTED {len(updates)} image-less products ===", flush=True)


if __name__ == "__main__":
    main()
