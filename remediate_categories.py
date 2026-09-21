# -*- coding: utf-8 -*-
"""One-off backlog cleanup: reclassify products stuck in the fallback category
'מוצרים נוספים' (id 3510) by PRECISE name-keyword rules.

Products created before a category/hint existed (e.g. jewelry before 'תכשיטים'
was added, mezuzahs the LLM split inconsistently) get stranded in the generic
bucket because the sync fast-path skips re-classifying existing products. This
rescues the obvious ones. Rules are deliberately conservative (include AND NOT
exclude) — a miss stays in fallback, never mis-filed.

    python remediate_categories.py            # DRY-RUN: report only
    python remediate_categories.py --apply
"""
import collections
import re
import sys
import warnings

warnings.filterwarnings("ignore")
from dotenv import load_dotenv

load_dotenv()

from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient

APPLY = "--apply" in sys.argv
FALLBACK_ID = 3510

# category name -> id (None = resolve live by exact-name search)
CATS = {
    "תכשיטים": 8014,
    "צמחים": 32,
    "מזוזות": None,
    "כוסות קידוש": None,
}

# (target, include-regex, exclude-regex). FIRST match wins; matches when
# include hits AND exclude does NOT.
RULES = [
    ("מזוזות", re.compile(r"מזוז"), re.compile(r"קופס|תיק |נרתיק")),
    ("כוסות קידוש",
     re.compile(r"גביע|כוס קידוש|כוס חתן|כוס אליהו|כוס של אליהו"),
     re.compile(r"תחתית|מעמד|סטנד|טס |מגש|סט ")),
    ("תכשיטים",
     re.compile(r"צמיד|שרשרת|טבעת|עגיל|תליון|כסף טהור 925"),
     re.compile(r"סטנד|מעמד|קופס|stand|תצוגה|עמדת")),
    ("צמחים",
     re.compile(r"ענף|בונסאי|סוקולנט|שתיל|פרח מלאכות|צמח מלאכות|עץ מלאכות|(?<!פ)זר "),
     re.compile(r"קער|צלח|כיסוי|בקבוק|סט |מגש|כוס|פמוט|נר ")),
]


def resolve(c, name):
    for t in c.get("products/categories", params={"search": name, "per_page": 20}):
        if t["name"] == name:
            return t["id"]
    return None


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)
    for k, v in list(CATS.items()):
        if v is None:
            CATS[k] = resolve(c, k)
            print(f"resolved {k} -> {CATS[k]}", flush=True)

    prods, page = [], 1
    while True:
        b = c.get("products", params={"per_page": 100, "page": page, "category": FALLBACK_ID,
                                      "status": "any", "_fields": "id,name"})
        if not b:
            break
        prods.extend(b)
        if len(b) < 100:
            break
        page += 1
    print(f"fallback products: {len(prods)}", flush=True)

    moves = collections.defaultdict(list)
    for p in prods:
        nm = p.get("name", "")
        for target, inc, exc in RULES:
            if inc.search(nm) and not exc.search(nm) and CATS.get(target):
                moves[target].append(p)
                break

    total = 0
    for target, items in moves.items():
        total += len(items)
        print(f"→ {target} ({CATS[target]}): {len(items)}", flush=True)
    print(f"total to move: {total} | staying: {len(prods) - total}", flush=True)

    if not APPLY:
        print("(DRY-RUN — no writes.)", flush=True)
        return

    updates = [{"id": p["id"], "categories": [{"id": CATS[target]}]}
               for target, items in moves.items() for p in items]
    done = 0
    for i in range(0, len(updates), 50):
        c.post("products/batch", {"update": updates[i:i + 50]})
        done += len(updates[i:i + 50])
        print(f"  …{done}/{len(updates)} moved", flush=True)
    print(f"=== APPLIED: moved {len(updates)} products out of fallback ===", flush=True)


if __name__ == "__main__":
    main()
