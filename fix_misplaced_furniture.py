# -*- coding: utf-8 -*-
"""Clean furniture categories: move products that don't belong (a vase in
'מזנונים', a salt-shaker in 'מיטות', a planter in 'כיסאות אוכל') to the category
the name clearly indicates, using the deterministic keyword classifier.

Only reassigns when the keyword rule gives a DIFFERENT existing category — a
high-confidence signal. Reversible (just a category change).

    python fix_misplaced_furniture.py            # DRY-RUN: list moves
    python fix_misplaced_furniture.py --apply
"""
import collections
import sys
import warnings

warnings.filterwarnings("ignore")
from dotenv import load_dotenv

load_dotenv()

from src.enrichment.keyword_rules import classify_by_rules
from src.woocommerce.client import WooCommerceClient
from src.core.config_loader import load_app_settings

APPLY = "--apply" in sys.argv

FURNITURE = ["ספות", "כורסאות", "שולחנות סלון", "שולחנות צד", "שולחנות אוכל",
             "כיסאות אוכל", "כיסאות בר", "הדומים", "מזנונים", "קונסולות ושידות כניסה",
             "קומודות ושידות", "מראות", "ספריות", "מיטות"]


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)

    # all store categories: name -> id. The store API intermittently returns an
    # empty page; retry so the category list is never silently truncated (an
    # incomplete list makes classify() skip valid targets -> missed moves).
    def get_retry(path, params, tries=6):
        for _ in range(tries):
            r = c.get(path, params=params)
            if r:
                return r
        return []

    name_to_id, page = {}, 1
    while True:
        b = get_retry("products/categories", {"per_page": 100, "page": page, "_fields": "id,name"})
        if not b:
            break
        for t in b:
            name_to_id[t["name"]] = t["id"]
        if len(b) < 100:
            break
        page += 1
    allowed = set(name_to_id)
    assert "חנוכה" in allowed and "מגשים" in allowed and len(allowed) > 50, \
        f"category list incomplete ({len(allowed)}) — aborting to avoid bad moves"

    moves = []          # (id, name, from_cat, to_cat)
    for fname in FURNITURE:
        fid = name_to_id.get(fname)
        if not fid:
            continue
        page = 1
        while True:
            b = get_retry("products", {"per_page": 100, "page": page, "category": fid,
                                       "status": "publish", "_fields": "id,name"})
            if not b:
                break
            for p in b:
                target = classify_by_rules(p.get("name", ""), allowed)
                # Move every product to the category its name indicates — including
                # furniture→furniture (a bar-chair in 'כיסאות אוכל' → 'כיסאות בר',
                # a side-table in 'שולחנות סלון' → 'שולחנות צד').
                if target and target != fname and target in name_to_id:
                    moves.append((p["id"], p.get("name", "")[:38], fname, target))
            if len(b) < 100:
                break
            page += 1

    by_target = collections.Counter(m[3] for m in moves)
    print(f"misplaced products to move: {len(moves)}", flush=True)
    for tgt, n in by_target.most_common():
        print(f"  -> {tgt}: {n}", flush=True)
    for pid, nm, frm, tgt in moves[:25]:
        print(f"   [{frm} -> {tgt}] {nm}", flush=True)
    if len(moves) > 25:
        print(f"   … +{len(moves)-25} more", flush=True)

    if not APPLY:
        print("(DRY-RUN — nothing changed.)", flush=True)
        return

    updates = [{"id": pid, "categories": [{"id": name_to_id[tgt]}]} for pid, _, _, tgt in moves]
    done = 0
    for i in range(0, len(updates), 50):
        c.post("products/batch", {"update": updates[i:i + 50]})
        done += len(updates[i:i + 50])
        print(f"  …{done}/{len(updates)} moved", flush=True)
    print(f"=== MOVED {len(updates)} products to correct categories ===", flush=True)


if __name__ == "__main__":
    main()
