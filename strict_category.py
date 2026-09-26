# -*- coding: utf-8 -*-
"""STRICT category enforcement: a product stays in <category> ONLY if the keyword
classifier confidently assigns it there. Everything else is relocated to the
category its name indicates, or to 'מוצרים נוספים' when no rule fits (so a
challah-cover / deco item can never sit under 'כיסאות אוכל').

    python strict_category.py "כיסאות אוכל"            # DRY-RUN
    python strict_category.py "כיסאות אוכל" --apply
"""
import sys, warnings, collections
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()

from src.enrichment.keyword_rules import classify_by_rules
from src.woocommerce.client import WooCommerceClient
from src.core.config_loader import load_app_settings

APPLY = "--apply" in sys.argv
FALLBACK = "מוצרים נוספים"
args = [a for a in sys.argv[1:] if not a.startswith("--")]
CATEGORY = args[0] if args else ""


def get_retry(c, path, params, tries=6):
    for _ in range(tries):
        r = c.get(path, params=params)
        if r:
            return r
    return []


def main():
    assert CATEGORY, "usage: strict_category.py <category name> [--apply]"
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)

    name_to_id, page = {}, 1
    while True:
        b = get_retry(c, "products/categories", {"per_page": 100, "page": page, "_fields": "id,name"})
        if not b:
            break
        for t in b:
            name_to_id[t["name"]] = t["id"]
        if len(b) < 100:
            break
        page += 1
    allowed = set(name_to_id)
    assert len(allowed) > 50 and CATEGORY in name_to_id and FALLBACK in name_to_id, \
        f"category list incomplete ({len(allowed)}) or '{CATEGORY}' not found"
    cid = name_to_id[CATEGORY]

    prods, page = [], 1
    while True:
        b = get_retry(c, "products", {"per_page": 100, "page": page, "category": cid,
                                      "status": "any", "_fields": "id,name"})
        if not b:
            break
        prods += b
        if len(b) < 100:
            break
        page += 1

    moves = []
    for p in prods:
        tgt = classify_by_rules(p.get("name", ""), allowed)
        if tgt == CATEGORY:
            continue                                  # confidently belongs → stay
        if not tgt or tgt not in name_to_id:
            tgt = FALLBACK
        if tgt == CATEGORY:
            continue                                  # no rule / already here → leave
        moves.append((p["id"], p.get("name", "")[:38], tgt))

    print(f"[{CATEGORY}] total={len(prods)} stay={len(prods)-len(moves)} move={len(moves)}", flush=True)
    for t, n in collections.Counter(m[2] for m in moves).most_common():
        print(f"  -> {t}: {n}", flush=True)
    for pid, nm, t in moves[:25]:
        print(f"   {nm} -> {t}", flush=True)
    if len(moves) > 25:
        print(f"   … +{len(moves)-25} more", flush=True)

    if not APPLY:
        print("(DRY-RUN)", flush=True)
        return
    updates = [{"id": pid, "categories": [{"id": name_to_id[t]}]} for pid, _, t in moves]
    for i in range(0, len(updates), 50):
        c.post("products/batch", {"update": updates[i:i + 50]})
    print(f"=== MOVED {len(updates)} out of {CATEGORY} ===", flush=True)


if __name__ == "__main__":
    main()
