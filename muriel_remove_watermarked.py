# -*- coding: utf-8 -*-
"""Remove Muriel products whose photo carries the 'מוריאל אומנות' watermark.

Those catalog entries are the ones titled exactly "מוריאל אומנות" (verified: the
supplier name is burned onto the top of the image). They must not be on the
store. Default = Trash (reversible); --force = permanent.

    python muriel_remove_watermarked.py            # DRY-RUN
    python muriel_remove_watermarked.py --apply
    python muriel_remove_watermarked.py --apply --force
"""
import sys
import warnings

warnings.filterwarnings("ignore")
import requests
from dotenv import load_dotenv

load_dotenv()

from src.woocommerce.client import WooCommerceClient
from src.core.config_loader import load_app_settings

APPLY = "--apply" in sys.argv
FORCE = "--force" in sys.argv
DATA_URL = "https://muriel-art-catalog.vercel.app/data.json"


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)

    data = requests.get(DATA_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=90).json()
    wm_ids = [it["id"] for it in data["items"] if (it.get("t") or "").strip() == "מוריאל אומנות"]
    print(f"watermarked source items (title == 'מוריאל אומנות'): {len(wm_ids)}", flush=True)

    removed, notfound = 0, 0
    for iid in wm_ids:
        sku = "MUR-" + iid
        try:
            r = c.get("products", params={"sku": sku, "_fields": "id,name"})
        except Exception:
            r = None
        if r:
            if APPLY:
                try:
                    c.delete(f"products/{r[0]['id']}", params={"force": FORCE})
                except Exception as exc:
                    print(f"  fail {sku}: {str(exc)[:50]}", flush=True)
                    continue
            removed += 1
        else:
            notfound += 1
    print(f"on store (removed): {removed} | not on store: {notfound}", flush=True)
    if APPLY:
        verb = "permanently deleted" if FORCE else "moved to Trash"
        print(f"=== {removed} watermarked Muriel products {verb} ===", flush=True)
    else:
        print("(DRY-RUN — nothing removed.)", flush=True)


if __name__ == "__main__":
    main()
