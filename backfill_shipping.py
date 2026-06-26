"""
backfill_shipping.py — assign a WooCommerce shipping class to every managed
product that currently has NONE, based on its category via
config/shipping_class_mapping.json. Does not touch products that already have
a shipping class.

Usage:
    python backfill_shipping.py            # dry-run (counts only)
    python backfill_shipping.py --apply    # write shipping_class
"""
import sys
import warnings
from collections import Counter

warnings.filterwarnings("ignore")

from src.core.config_loader import load_app_settings, load_shipping_class_mapping
from src.core.constants import META_SYNC_MANAGED
from src.woocommerce.client import WooCommerceClient


def main():
    apply = "--apply" in sys.argv
    s = load_app_settings()
    mapping = load_shipping_class_mapping()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl,
                          dry_run=False)

    print("Loading all products...")
    allp = c.get_all("products", status="any")

    to_set = []           # (id, name, category, class)
    no_category_match = Counter()
    already = 0
    for p in allp:
        meta = {m["key"]: m["value"] for m in p.get("meta_data", [])}
        if str(meta.get(META_SYNC_MANAGED, "")).lower() not in ("true", "1"):
            continue
        # already has a shipping class?
        if (p.get("shipping_class") or "") or p.get("shipping_class_id"):
            already += 1
            continue
        cat_names = [x.get("name", "") for x in p.get("categories", [])]
        chosen = None
        for cn in cat_names:
            if cn in mapping:
                chosen = (cn, mapping[cn])
                break
        if not chosen:
            for cn in cat_names:
                no_category_match[cn] += 1
            continue
        to_set.append((p["id"], (p.get("name") or "")[:32], chosen[0], chosen[1]))

    by_class = Counter(cls for _, _, _, cls in to_set)
    print(f"\nManaged products: {already} already have a class | "
          f"{len(to_set)} will get one | {sum(no_category_match.values())} have no mappable category")
    print("Assignments by class:")
    for cls, n in by_class.most_common():
        print(f"   {n:>5}  → {cls}")
    if no_category_match:
        print("Categories with NO shipping mapping (left empty):")
        for cn, n in no_category_match.most_common(20):
            print(f"   {n:>5}  {cn}")

    if not apply:
        print("\n(DRY-RUN — re-run with --apply to write shipping classes)")
        return 0

    done = 0
    for pid, nm, cat, cls in to_set:
        try:
            c.put(f"products/{pid}", {"shipping_class": cls})
            done += 1
            if done % 100 == 0:
                print(f"   ...{done}/{len(to_set)}")
        except Exception as exc:
            print(f"   ✗ {pid} {nm}: {str(exc)[:100]}")
    print(f"\nSet shipping class on {done}/{len(to_set)} products.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
