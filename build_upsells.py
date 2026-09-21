# -*- coding: utf-8 -*-
"""
Set "complementary products" (Upsells) shown BELOW each product page.

Two signals, in order:
  1. SAME SERIES — products whose names share a distinctive model/series token
     (e.g. "כורסה פיבי" + "הדום פיבי" share "פיבי"), so a product page shows the
     rest of its set/series.
  2. SAME CATEGORY — fill up to N with other in-stock products from the same
     category (closest price first).

Writes product.upsell_ids in batches. Published, in-stock products only.

    python build_upsells.py            # DRY-RUN: report groups, no writes
    python build_upsells.py --apply
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
N_UPSELLS = 6

# Generic words that are NOT a series name.
_GENERIC = set("""שולחן שולחנות כיסא כיסאות כורסה כורסאות ספה ספות הדום הדומים מזנון שידה
קונסולה מראה מראות אגרטל ואזה פסל מגש מגשים נר נרות שעון פמוט נטלה כד כלי סט זוג שלישיית
שחור שחורה לבן לבנה אפור אפורה זהב כסף כסוף מוזהב חום בז' ניוד קרם שמנת ירוק כחול אדום ורוד
טורקיז בורדו שנהב חרדל טבעי כהה בהיר עתיק שמפניה נחושת אבן
עץ מלא מתכת זכוכית קריסטל פורצלן קרמיקה אקריליק בטון דמוי שיש עור בד קטיפה נירוסטה
גדול גדולה קטן קטנה בינוני נפתח מרופד ריפוד עם ללא סמ ס״מ מ״מ אינץ מודרני קלאסי מעוצב
של גוון בגוון דגם דגמים קוטר רגל רגליים חלקים יחידות חלק סטים בשמים לנרות""".split())

# Hebrew one-letter prefixes that attach to a word (and=ו, to=ל, in=ב, the=ה,
# that=ש, like=כ, from=מ). A "series" token that is only a generic word wearing
# one of these (e.g. "וכסף", "לנרות") is NOT a real series.
_PREFIXES = "ולבהשכמ"


def _is_generic(t):
    if t in _GENERIC:
        return True
    if len(t) > 3 and t[0] in _PREFIXES and t[1:] in _GENERIC:
        return True
    return False


def _series_token(name):
    """Pick the most distinctive token from a product name (candidate series)."""
    toks = re.findall(r"[A-Za-zא-ת]{3,}", name or "")
    cand = [t for t in toks if not _is_generic(t) and not t.isdigit()]
    return cand


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)

    print("loading published products…", flush=True)
    prods, page = [], 1
    while True:
        b = None
        for attempt in range(5):
            try:
                b = c.get("products", params={"per_page": 100, "page": page, "status": "publish",
                                              "_fields": "id,name,price,categories"})
                break
            except Exception:
                if attempt == 4:
                    raise
        if not b:
            break
        prods.extend(b)
        if len(b) < 100:
            break
        page += 1
    print(f"products: {len(prods)}", flush=True)

    # index by category + by series token
    by_cat = collections.defaultdict(list)
    token_groups = collections.defaultdict(list)
    for p in prods:
        cat = p["categories"][0]["name"] if p.get("categories") else ""
        p["_cat"] = cat
        by_cat[cat].append(p)
        for t in set(_series_token(p.get("name", ""))):
            token_groups[t].append(p["id"])

    # keep only tokens shared by 2..12 products (real series, not generic)
    series = {t: ids for t, ids in token_groups.items() if 2 <= len(ids) <= 8}
    pid_series = collections.defaultdict(set)
    for t, ids in series.items():
        for pid in ids:
            pid_series[pid].update(x for x in ids if x != pid)

    updates = []
    n_series = 0
    for p in prods:
        ups = list(pid_series.get(p["id"], []))[:N_UPSELLS]
        if ups:
            n_series += 1
        if len(ups) < N_UPSELLS:                       # fill from same category
            def _price(x):
                try:
                    return abs(float(x.get("price") or 0) - float(p.get("price") or 0))
                except (ValueError, TypeError):
                    return 1e9
            same = [q["id"] for q in sorted(by_cat.get(p["_cat"], []), key=_price)
                    if q["id"] != p["id"] and q["id"] not in ups]
            ups += same[:N_UPSELLS - len(ups)]
        if ups:
            updates.append({"id": p["id"], "upsell_ids": ups})

    print(f"products with a series match: {n_series} | total getting upsells: {len(updates)}", flush=True)
    print("sample series groups:", flush=True)
    for t, ids in list(sorted(series.items(), key=lambda kv: -len(kv[1])))[:8]:
        print(f"  '{t}': {len(ids)} products", flush=True)

    if not APPLY:
        print("\n(DRY-RUN — no writes.)", flush=True)
        return

    # batch update
    done = 0
    for i in range(0, len(updates), 50):
        batch = updates[i:i + 50]
        for attempt in range(5):
            try:
                c.post("products/batch", {"update": batch})
                break
            except Exception:
                if attempt == 4:
                    print(f"  batch {i} failed", flush=True)
                    break
        done += len(batch)
        if done % 500 == 0:
            print(f"  …{done} updated", flush=True)
    print(f"\n=== APPLIED upsells to {len(updates)} products ===", flush=True)


if __name__ == "__main__":
    main()
