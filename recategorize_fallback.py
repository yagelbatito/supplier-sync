"""
recategorize_fallback.py — move managed PUBLISHED products that are currently
sitting in the fallback category ("בדיקה ידנית") into a proper category, using
a clean keyword→category map (targets verified to exist in WooCommerce).

Usage:
    python recategorize_fallback.py            # dry-run (prints what would change)
    python recategorize_fallback.py --apply    # actually update categories
"""
import re
import sys
import warnings
from collections import Counter

warnings.filterwarnings("ignore")

from src.core.config_loader import load_app_settings, load_suppliers
from src.core.constants import META_SYNC_MANAGED, META_SUPPLIER_NAME
from src.woocommerce.client import WooCommerceClient

FALLBACK = "בדיקה ידנית"

# Ordered longest/most-specific FIRST. Targets must exist in WooCommerce.
MAPPING = [
    ("וול שלף", "אקססוריז"),
    ("שולחן סלון", "שולחנות סלון"),
    ("שולחן צד", "שולחנות צד"),
    ("שולחן אוכל", "שולחנות"),
    ("פינת אוכל", "פינות אוכל מעץ"),
    ("כיסא בר", "כיסאות בר"),
    ("כסא בר", "כיסאות בר"),
    ("סוקולנט", "צמחים"),
    ("סוקלנט", "צמחים"),
    ("פלנטר", "צמחים"),
    ("קונסולה", "קונסולות ושידות כניסה"),
    ("קונסול", "קונסולות ושידות כניסה"),
    ("נברשת", "תאורה"),
    ("מנורת", "תאורה"),
    ("מנורה", "תאורה"),
    ("שולחן", "שולחנות"),
    ("עציץ", "צמחים"),
    ("ענף", "צמחים"),
    ("פרח", "צמחים"),
    ("ליבס", "צמחים"),
    ("ואזה", "אקססוריז"),
    ("פמוט", "אקססוריז"),
    ("מדפי", "אקססוריז"),
    ("מדף", "אקססוריז"),
    ("סטנד", "אקססוריז"),
    ("הנגר", "אקססוריז"),
    ("פסל", "אקססוריז"),
    ("מראות", "מראות"),
    ("מירור", "מראות"),
    ("מראה", "מראות"),
    ("כיסא", "כיסאות"),
    ("כסא", "כיסאות"),
    ("שמיכת", "טקסטיל"),
    ("שמיכה", "טקסטיל"),
    ("שמיכות", "טקסטיל"),
    # ── high-confidence extras for the long tail ──
    ("אייר פלנט", "צמחים"),
    ("אירפלנט", "צמחים"),
    ("גן עדן", "צמחים"),
    ("אורכיד", "צמחים"),
    ("קקטוס", "צמחים"),
    ("אגרטל", "אקססוריז"),
    ("מטאל באקט", "אקססוריז"),
    ("באקט", "אקססוריז"),
    ("ספרי קופסה", "אקססוריז"),
    ("מידוף", "אקססוריז"),
    ("שעון", "שעונים"),
    ("שידת", "קומודות ושידות"),
    ("שידה", "קומודות ושידות"),
    ("כד", "אקססוריז"),   # short → word-boundary matched below
    ("עץ", "צמחים"),       # short → word-boundary; trees (material 'עץ' already caught earlier by furniture keywords)
]


def matches(name, kw):
    if len(kw) <= 2:
        pat = rf"(?<![\w֐-׿]){re.escape(kw)}(?![\w֐-׿])"
        return re.search(pat, name) is not None
    return kw in name


def pick_category(name):
    for kw, cat in MAPPING:
        if matches(name, kw):
            return cat, kw
    return None, None


def main():
    apply = "--apply" in sys.argv
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl,
                          dry_run=False)

    # resolve category name → id
    cats = c.get_all("products/categories")
    name2id = {x["name"].strip(): x["id"] for x in cats if x.get("id")}
    for _, cat in MAPPING:
        if cat not in name2id:
            print(f"⚠️  target category missing in WC: '{cat}' — its rules will be skipped")

    keys = {v.supplier_name.lower(): k for k, v in load_suppliers().items()}
    print("Loading all published products...")
    allp = c.get_all("products", status="publish")

    targets = []
    for p in allp:
        meta = {m["key"]: m["value"] for m in p.get("meta_data", [])}
        if str(meta.get(META_SYNC_MANAGED, "")).lower() not in ("true", "1"):
            continue
        cat_names = [x.get("name", "") for x in p.get("categories", [])]
        if cat_names == [FALLBACK]:
            targets.append(p)

    print(f"Found {len(targets)} managed published products in fallback category\n")

    changed = Counter()
    unmatched = []
    for p in targets:
        cat, kw = pick_category(p.get("name", ""))
        if not cat or cat not in name2id:
            unmatched.append(p.get("name", ""))
            continue
        changed[cat] += 1
        if apply:
            try:
                c.put(f"products/{p['id']}", {"categories": [{"id": name2id[cat]}]})
            except Exception as exc:
                print(f"  ✗ {p['id']} {p.get('name','')[:40]}: {exc}")

    verb = "MOVED" if apply else "WOULD MOVE"
    print(f"=== {verb} {sum(changed.values())} products → categories ===")
    for cat, n in changed.most_common():
        print(f"   {n:>4}  → {cat}")
    print(f"\n=== {len(unmatched)} remain unmatched (still fallback) ===")
    for nm in unmatched[:40]:
        print("   ?", nm)
    if len(unmatched) > 40:
        print(f"   ... and {len(unmatched)-40} more")
    if not apply:
        print("\n(DRY-RUN — re-run with --apply to write changes)")


if __name__ == "__main__":
    sys.exit(main())
