# -*- coding: utf-8 -*-
"""Clear the remaining שולחנות category:
  • מעמד לסירים            → כלי מטבח
  • שולחן מרטינה           → שולחנות אוכל  (specific, before the generic rule)
  • סט שולחנות / שולחן / שולחנות → שולחנות סלון (catch-all for the rest)
Dry-run unless --apply. Dumps moved names to data/shulchan_finish_names.json."""
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
KITCHEN = "כלי מטבח" if cat_svc.resolve("כלי מטבח", fallback="") else "מטבח ואירוח"
print(f"pot-stand target = '{KITCHEN}' (כלי מטבח exists={bool(cat_svc.resolve('כלי מטבח', fallback=''))})", flush=True)

def target(n):
    if "מעמד לסירים" in n:
        return KITCHEN
    if "שולחן גן" in n:          # garden tables — leave; ask owner
        return None
    if "מרטינה" in n:
        return "שולחנות אוכל"
    if any(x in n for x in ("סט שולחנות", "סט שולנות", "שולחנות", "שולנות", "שולחן")):
        return "שולחנות סלון"
    return None

SRC = cat_svc.resolve("שולחנות", fallback="")
prods = c.get_all("products", category=str(SRC), status="any")
print(f"=== SHULCHAN FINISH ({'APPLY' if APPLY else 'DRY-RUN'}) · שולחנות({SRC}): {len(prods)} products ===", flush=True)
counts = collections.Counter(); moved = collections.defaultdict(list)
for p in prods:
    nm = (p.get("name") or "").strip()
    tgt = target(nm)
    if not tgt:
        print(f"   [LEFT        ] {nm[:46]}", flush=True); continue
    tid = cat_svc.resolve(tgt, fallback="")
    counts[tgt] += 1; moved[tgt].append(nm)
    print(f"   [{tgt[:12]:<12}] {nm[:46]}", flush=True)
    if APPLY:
        c.put(f"products/{p['id']}", {"categories": [{"id": tid}]})
with open("data/shulchan_finish_names.json", "w", encoding="utf-8") as f:
    json.dump(moved, f, ensure_ascii=False, indent=2)
print("\n" + " · ".join(f"{k}:{v}" for k, v in counts.most_common()), flush=True)
print(f"=== moved={sum(counts.values())}/{len(prods)} ({'APPLIED' if APPLY else 'dry-run'}) ===", flush=True)
