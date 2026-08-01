# -*- coding: utf-8 -*-
"""
Owner sweep over two categories, by product name.

כורסאות:
  • נשכן / נשכנים                 → שולחנות צד
  • ספת סלון / ספה…               → ספות

שולחנות (ordered — specific before general):
  1. שולחן פינת אוכל              → שולחנות אוכל
  2. כף הגשה                      → מטבח ואירוח
  3. פלייסמנט                     → מפות
  4. קונסולה/קונסולות/קונזולה      → קונסולות ושידות כניסה
  5. שולחן מתכת מודרני            → שולחנות צד   (before general שולחן מתכת)
  6. סטול / הדום / הדומים          → הדומים
  7. כיסא (ולא כיסא בר)            → כיסאות אוכל
  8. סט שולחנות / שולחנות (רבים)   → שולחנות סלון
  9. שולחן שיש/מתכת/עץ/אורגניק/גולד → שולחנות סלון

Move = replace category with target. Dry-run unless --apply. Dumps moved exact
names to data/sweep3_moved_names.json for the mapping step.
"""
import sys, io, json, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
import warnings; warnings.filterwarnings("ignore")
from dotenv import load_dotenv; load_dotenv()
from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient
from src.woocommerce.category_service import CategoryService

APPLY = "--apply" in sys.argv
s = load_app_settings()
c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                      consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                      wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=False)
cat_svc = CategoryService(c); cat_svc.load()


def target_kur(n):
    if "נשכן" in n:
        return "שולחנות צד"
    if ("ספת סלון" in n) or n.startswith("ספה") or n.startswith("ספת"):
        return "ספות"
    return None


def target_shu(n):
    if "שולחן פינת אוכל" in n:
        return "שולחנות אוכל"
    if "כף הגשה" in n:
        return "מטבח ואירוח"
    if "פלייסמנט" in n:
        return "מפות"
    if ("קונסולה" in n) or ("קונסולות" in n) or ("קונזולה" in n):
        return "קונסולות ושידות כניסה"
    if "שולחן מתכת מודרני" in n:
        return "שולחנות צד"
    # stool/hadom — but NOT when the item is primarily a table (starts "שולחן"),
    # e.g. "שולחן שיש והדום" is a table set, belongs in שולחנות סלון.
    if (("סטול" in n) or ("הדום" in n) or ("הדומים" in n)) and not n.startswith("שולחן"):
        return "הדומים"
    if ("כיסא" in n or "כסא" in n) and ("כיסא בר" not in n) and ("כסא בר" not in n):
        return "כיסאות אוכל"
    if ("סט שולחנות" in n) or ("שולחנות" in n):
        return "שולחנות סלון"
    if any(x in n for x in ("שולחן שיש", "שולחן מתכת", "שולחן עץ", "שולחן אורגניק", "שולחן גולד")):
        return "שולחנות סלון"
    return None


def run(cat_name, fn):
    cid = cat_svc.resolve(cat_name, fallback="")
    prods = c.get_all("products", category=str(cid), status="any")
    print(f"\n### {cat_name} (id={cid}): {len(prods)} products", flush=True)
    counts = collections.Counter()
    for p in prods:
        nm = (p.get("name") or "").strip()
        tgt = fn(nm)
        if not tgt:
            continue
        tid = cat_svc.resolve(tgt, fallback="")
        if not tid:
            print(f"   (target '{tgt}' missing!)", flush=True); continue
        counts[tgt] += 1
        moved[tgt].append(nm)
        print(f"   [{tgt[:14]:<14}] {nm[:44]}", flush=True)
        if APPLY:
            try:
                c.put(f"products/{p['id']}", {"categories": [{"id": tid}]})
            except Exception as e:
                print(f"      fail {p['id']}: {e}", flush=True)
    for k, v in counts.most_common():
        print(f"   → {k}: {v}", flush=True)
    return sum(counts.values()), len(prods)


print(f"=== CAT SWEEP 3 ({'APPLY' if APPLY else 'DRY-RUN'}) ===", flush=True)
moved = collections.defaultdict(list)
m1, t1 = run("כורסאות", target_kur)
m2, t2 = run("שולחנות", target_shu)
with open("data/sweep3_moved_names.json", "w", encoding="utf-8") as f:
    json.dump(moved, f, ensure_ascii=False, indent=2)
print(f"\n=== DONE. moved: כורסאות={m1}/{t1} · שולחנות={m2}/{t2} "
      f"({'APPLIED' if APPLY else 'dry-run'}) → data/sweep3_moved_names.json ===", flush=True)
