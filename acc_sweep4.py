# -*- coding: utf-8 -*-
"""
אקססוריז:
  • מעמד + קומות/קומותיים     → פעמוני עוגה
  • כביסה                     → סלי כביסה
  • מפיון / מתקן לסכו"ם        → סכו"ם
  • קערה / קערות              → כלי מטבח
  • פסל (leftovers)           → פסלים
כלי מטבח:
  • פח / פח אשפה              → פחים
(ואזה rule handled separately — pending clarification.)
Dry-run unless --apply. Dumps names to data/sweep4_moved.json.
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
moved = collections.defaultdict(list)


def target_acc(n):
    if ("מעמד" in n) and (("קומות" in n) or ("קומותיים" in n) or ("קומתיים" in n)):
        return "פעמוני עוגה"
    if "כביסה" in n:
        return "סלי כביסה"
    if ("מפיון" in n) or ("מתקן לסכו" in n) or ("מתקן סכו" in n) or ("מחזיק סכו" in n):
        return "סכו\"ם"
    if ("קערה" in n) or ("קערות" in n) or ("קערת" in n):
        return "כלי מטבח"
    if "פסל" in n:
        return "פסלים"
    return None


def target_kitchen(n):
    if n.startswith("פח") or ("פח אשפה" in n) or ("פח " in n):
        return "פחים"
    return None


def run(src_name, fn):
    src = cat_svc.resolve(src_name, fallback="")
    prods = c.get_all("products", category=str(src), status="any")
    print(f"\n### {src_name}({src}): {len(prods)} products", flush=True)
    counts = collections.Counter()
    for p in prods:
        nm = (p.get("name") or "").strip()
        tgt = fn(nm)
        if not tgt:
            continue
        tid = cat_svc.resolve(tgt, fallback="")
        counts[tgt] += 1
        if len(moved[tgt]) < 6:
            moved[tgt].append(nm[:38])
        if APPLY and tid:
            c.put(f"products/{p['id']}", {"categories": [{"id": tid}]})
    for k, v in counts.most_common():
        print(f"   → {k}: {v}", flush=True)
    return sum(counts.values())


print(f"=== ACC SWEEP 4 ({'APPLY' if APPLY else 'DRY-RUN'}) ===", flush=True)
n1 = run("אקססוריז", target_acc)
n2 = run("כלי מטבח", target_kitchen)
json.dump(moved, open("data/sweep4_moved.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("\n=== samples ===", flush=True)
for k, v in moved.items():
    print(f"  {k}:", flush=True)
    for ex in v:
        print(f"     · {ex}", flush=True)
print(f"\n=== total={n1 + n2} ({'APPLIED' if APPLY else 'dry-run'}) ===", flush=True)
