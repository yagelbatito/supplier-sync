# -*- coding: utf-8 -*-
"""
Sort the "מזנונים" category by product name:
  • ויטרינה / ויטרינות            → ספריות
  • שידת צד / שידות צד            → שולחנות צד
  • כורסא / כורסה / כורסאות       → כורסאות
  • כיסא בר / כסא בר / כיסאות בר   → כיסאות בר
  • כיסא אוכל / כסא אוכל / כיסאות אוכל → כיסאות אוכל
  • שולחן סלון / שולחנות סלון      → שולחנות סלון
Move = replace category with the target. Dry-run unless --apply.
Also prints the exact moved names grouped by target (for the mapping step).
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
MEZ_ID = cat_svc.resolve("מזנונים", fallback="")

def target(nm):
    n = nm
    if ("ויטרינה" in n) or ("ויטרינות" in n):
        return "ספריות"
    if ("שידת צד" in n) or ("שידות צד" in n):
        return "שולחנות צד"
    if ("כורסא" in n) or ("כורסה" in n) or ("כורסאות" in n) or ("כורסת" in n):
        return "כורסאות"
    if ("כיסא בר" in n) or ("כסא בר" in n) or ("כיסאות בר" in n) or ("כסאות בר" in n):
        return "כיסאות בר"
    if ("כיסא אוכל" in n) or ("כסא אוכל" in n) or ("כיסאות אוכל" in n) or ("כסאות אוכל" in n):
        return "כיסאות אוכל"
    if ("שולחן סלון" in n) or ("שולחנות סלון" in n):
        return "שולחנות סלון"
    return None

print(f"=== MEZ SWEEP ({'APPLY' if APPLY else 'DRY-RUN'}) ===", flush=True)
prods = c.get_all("products", category=str(MEZ_ID), status="any")
print(f"'מזנונים' (id={MEZ_ID}): {len(prods)} products\n", flush=True)

counts = collections.Counter()
moved_names = collections.defaultdict(list)
for p in prods:
    nm = (p.get("name") or "").strip()
    tgt = target(nm)
    if not tgt:
        continue
    tid = cat_svc.resolve(tgt, fallback="")
    if not tid:
        print(f"   (target '{tgt}' missing!)", flush=True); continue
    counts[tgt] += 1
    moved_names[tgt].append(nm)
    print(f"   [{tgt[:12]:<12}] {nm[:46]}", flush=True)
    if APPLY:
        try:
            c.put(f"products/{p['id']}", {"categories": [{"id": tid}]})
        except Exception as e:
            print(f"      fail {p['id']}: {e}", flush=True)

print("\n=== summary ===", flush=True)
for k, v in counts.most_common():
    print(f"   {v:4}  → {k}", flush=True)
left = len(prods) - sum(counts.values())
print(f"moved={sum(counts.values())} · left in מזנונים={left}", flush=True)

# dump exact names for the mapping step
with open("data/mez_moved_names.json", "w", encoding="utf-8") as f:
    json.dump(moved_names, f, ensure_ascii=False, indent=2)
print("exact moved names → data/mez_moved_names.json", flush=True)
