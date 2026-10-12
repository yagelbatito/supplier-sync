# -*- coding: utf-8 -*-
"""Weekly stock refresh for Home Craft products (scheduled every Monday).

Re-reads Home Craft's live catalog and updates each already-imported product's
stock status on the store: anything that has sold out at Home Craft is set to
out-of-stock here, and anything back in stock is re-enabled. For the colour
variable products it updates each colour variation independently. It never
creates or deletes products and never touches prices or content — stock only.

    python hcraft_stock_sync.py            # DRY-RUN (report changes)
    python hcraft_stock_sync.py --apply
"""
import sys
import warnings

warnings.filterwarnings("ignore")
from dotenv import load_dotenv

load_dotenv()

import hcraft_common as H
from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient

APPLY = "--apply" in sys.argv


def main():
    s = load_app_settings()
    wc = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                           consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                           wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)

    tok = H.get_token()
    cats = H.categories(tok)

    stats = {"checked": 0, "unchanged": 0, "updated": 0, "var_updated": 0, "missing": 0, "failed": 0}
    for cat_name, (_store_cat, _mult) in H.TARGETS.items():
        prods = H.fetch_category(tok, cats[cat_name])
        for g in H.group_products(prods):
            rep_color, rep = g["members"][0]
            sku = f"{H.SKU_PREFIX}-{rep['id']}"
            stats["checked"] += 1
            try:
                found = wc.get("products", params={"sku": sku, "_fields": "id,type,stock_status"})
            except Exception:
                found = None
            if not found:
                stats["missing"] += 1
                continue
            prod = found[0]
            pid = prod["id"]

            if g["variable"]:
                # per-colour: match each sibling's live stock to its variation
                want = {}
                for color, m in g["members"]:
                    if color:
                        want[color] = "instock" if m.get("isInStock") else "outofstock"
                try:
                    variations = wc.get(f"products/{pid}/variations",
                                        params={"per_page": 100, "_fields": "id,stock_status,attributes"})
                except Exception:
                    variations = []
                for v in (variations or []):
                    opt = (v.get("attributes") or [{}])[0].get("option", "")
                    target = want.get(opt)
                    if target and target != v.get("stock_status"):
                        if APPLY:
                            try:
                                wc.put(f"products/{pid}/variations/{v['id']}", {"stock_status": target})
                            except Exception:
                                stats["failed"] += 1
                                continue
                        stats["var_updated"] += 1
                        print(f"  {sku} [{opt}] -> {target}", flush=True)
                continue

            target = "instock" if rep.get("isInStock") else "outofstock"
            if target == prod.get("stock_status"):
                stats["unchanged"] += 1
                continue
            if APPLY:
                try:
                    wc.put(f"products/{pid}", {"stock_status": target, "manage_stock": False})
                except Exception:
                    stats["failed"] += 1
                    continue
            stats["updated"] += 1
            print(f"  {sku} {prod.get('stock_status')} -> {target}", flush=True)

    print(f"\n=== Home Craft stock sync {'APPLIED' if APPLY else 'DRY-RUN'} ===", flush=True)
    print(stats, flush=True)


if __name__ == "__main__":
    main()
