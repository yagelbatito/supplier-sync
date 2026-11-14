# -*- coding: utf-8 -*-
"""One-time: clean promo overlays off every existing M.M.S product image.

For each M.M.S product it downloads the main image, removes the clearance badge
and the bottom info-banner (src/suppliers/mms_image_clean.clean), and re-uploads
the clean version. When the badge carries a price, that is the wholesale/clearance
price, so the store price is set to 2x it and pinned in config/mms_price_overrides.json
(so the weekly/daily sync won't revert it). Marks each product with meta
`_mms_img_cleaned=1`, so the run is resumable and re-runs skip finished products.

    python mms_clean_all.py              # DRY-RUN (report only)
    python mms_clean_all.py --apply
    python mms_clean_all.py --apply --limit=20
"""
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
import cv2
import numpy as np
import requests
from dotenv import load_dotenv

load_dotenv()

from src.core.config_loader import load_app_settings
from src.enrichment.openai_client import OpenAIClient
from src.suppliers import mms_image_clean as mic
from src.woocommerce.client import WooCommerceClient

APPLY = "--apply" in sys.argv
LIMIT = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--limit=")), 0)
PRICE_MULT_FROM_CIRCLE = 2.0
OVERRIDES_PATH = os.path.join("config", "mms_price_overrides.json")
CLEAN_FLAG = "_mms_img_cleaned"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"


def meta_get(p, key):
    for m in p.get("meta_data", []):
        if m.get("key") == key:
            return m.get("value")
    return None


def load_overrides():
    try:
        with open(OVERRIDES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_overrides(d):
    with open(OVERRIDES_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)
    ai = OpenAIClient(os.getenv("OPENAI_API_KEY", ""), model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    sess = requests.Session(); sess.headers["User-Agent"] = UA
    overrides = load_overrides()

    stats = {"mms": 0, "circle": 0, "banner": 0, "repriced": 0, "cleaned": 0,
             "skipped_done": 0, "no_overlay": 0, "failed": 0}
    done = 0
    page = 1
    while True:
        batch = None
        for _ in range(6):
            batch = c.get("products", params={"per_page": 100, "page": page, "status": "any",
                                              "_fields": "id,sku,name,regular_price,images,meta_data"})
            if batch is not None:
                break
        if not batch:
            break
        for p in batch:
            sku = p.get("sku", "") or ""
            is_mms = (meta_get(p, "supplier_name") == "M.M.S") or sku.startswith("MMS-")
            if not is_mms:
                continue
            stats["mms"] += 1
            if meta_get(p, CLEAN_FLAG) == "1":
                stats["skipped_done"] += 1
                continue
            imgs = p.get("images") or []
            if not imgs or not imgs[0].get("src"):
                continue
            try:
                r = sess.get(imgs[0]["src"], timeout=40)
                arr = np.frombuffer(r.content, np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    raise ValueError("decode fail")
            except Exception as exc:
                stats["failed"] += 1
                print(f"  dl/decode fail {sku}: {str(exc)[:50]}", flush=True)
                continue

            clean_img, info = mic.clean(img, ai=ai if APPLY else None)
            has_circle = info["circle"] is not None
            has_banner = info["banner"] is not None
            cprice = info["circle_price"]
            if has_circle:
                stats["circle"] += 1
            if has_banner:
                stats["banner"] += 1

            if not has_circle and not has_banner:
                stats["no_overlay"] += 1
                if APPLY:  # mark so we don't re-download it next run
                    try:
                        c.put(f"products/{p['id']}", {"meta_data": [{"key": CLEAN_FLAG, "value": "1"}]})
                    except Exception:
                        pass
                continue

            new_price = int(round(cprice * PRICE_MULT_FROM_CIRCLE)) if cprice else None
            done += 1
            if not APPLY:
                print(f"  {sku} {p.get('name','')[:30]}: circle={has_circle} banner={has_banner} "
                      f"circle_price={cprice} -> price={new_price if new_price else 'keep'}")
                if LIMIT and done >= LIMIT:
                    break
                continue

            # re-upload the cleaned image
            try:
                ok, buf = cv2.imencode(".jpg", clean_img, [cv2.IMWRITE_JPEG_QUALITY, 92])
                media_id = c.upload_media(buf.tobytes(), f"{sku.lower()}-clean.jpg", "image/jpeg")
                payload = {"images": [{"id": media_id}] if media_id else [],
                           "meta_data": [{"key": CLEAN_FLAG, "value": "1"}]}
                if new_price:
                    payload["regular_price"] = str(new_price)
                    payload["meta_data"] += [
                        {"key": "original_supplier_price", "value": str(cprice)},
                        {"key": "calculated_store_price", "value": str(new_price)},
                    ]
                c.put(f"products/{p['id']}", payload)
                stats["cleaned"] += 1
                if new_price:
                    overrides[sku] = new_price
                    stats["repriced"] += 1
                if stats["cleaned"] % 25 == 0:
                    print(f"  …cleaned {stats['cleaned']}", flush=True)
            except Exception as exc:
                stats["failed"] += 1
                print(f"  clean fail {sku}: {str(exc)[:60]}", flush=True)
            if LIMIT and done >= LIMIT:
                break
        if LIMIT and done >= LIMIT:
            break
        if len(batch) < 100:
            break
        page += 1

    if APPLY:
        save_overrides(overrides)
    print(f"\n=== M.M.S image clean {'APPLIED' if APPLY else 'DRY-RUN'} ===", flush=True)
    print(stats, flush=True)
    print(f"overrides now: {len(overrides)} SKUs", flush=True)


if __name__ == "__main__":
    main()
