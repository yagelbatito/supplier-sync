# -*- coding: utf-8 -*-
"""
Restructure the אקססוריז category.

Create child categories under אקססוריז (id 26): אגרטלים וואזות, בתי עציץ,
פסלים, כדים. Create 'פחים' under מטבח ואירוח (id 31). Then move by name:
  • מברשת / פח-שירותים                → אביזרי אמבטיה
  • פח (מטבח)                         → פחים
  • בית עציץ / בתי עציץ / פלנטר         → בתי עציץ
  • אגרטל / ואזה / vase               → אגרטלים וואזות
  • כד (לא כדור) / כד קרמיקה           → כדים
  • פסל                               → פסלים
  • שעון                              → שעונים (existing)
The ~1180 unmatched stay in אקססוריז. Dry-run unless --apply.
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
ACC = cat_svc.resolve("אקססוריז", fallback="")
KITCHEN = cat_svc.resolve("מטבח ואירוח", fallback="")

# categories to ensure exist: name -> parent id
NEW_CATS = {
    "אגרטלים וואזות": ACC, "בתי עציץ": ACC, "פסלים": ACC, "כדים": ACC,
    "פחים": KITCHEN,
}


def ensure(name, parent):
    cid = cat_svc.resolve(name, fallback="")
    if cid:
        return cid
    if APPLY:
        res = c.post("products/categories", {"name": name, "parent": parent})
        cid = res.get("id"); cat_svc._categories[name] = cid
        print(f"created '{name}' id={cid} (parent {parent})", flush=True)
        return cid
    print(f"would CREATE '{name}' (parent {parent})", flush=True)
    return None


def bucket(n):
    if "מברשת" in n:
        return "אביזרי אמבטיה"
    if n.startswith("פח") or ("פח אשפה" in n) or ("פח " in n):
        if any(x in n for x in ("שירות", "אמבט", "רחצ")):
            return "אביזרי אמבטיה"
        return "פחים"
    if ("בית עציץ" in n) or ("בתי עציץ" in n) or ("פלנטר" in n):
        return "בתי עציץ"
    if ("אגרטל" in n) or ("ואזה" in n) or ("vase" in n.lower()):
        return "אגרטלים וואזות"
    if ("כד קרמיקה" in n) or ("כדים" in n) or n.startswith("כד "):
        if "כדור" in n:
            return None
        return "כדים"
    if "פסל" in n:
        return "פסלים"
    if "שעון" in n:
        return "שעונים"
    return None


print(f"=== ACC RESTRUCTURE ({'APPLY' if APPLY else 'DRY-RUN'}) ===", flush=True)
for name, parent in NEW_CATS.items():
    ensure(name, parent)

prods = c.get_all("products", category=str(ACC), status="any")
print(f"אקססוריז: {len(prods)} products", flush=True)
counts = collections.Counter(); moved = collections.defaultdict(list)
for p in prods:
    nm = (p.get("name") or "").strip()
    tgt = bucket(nm)
    if not tgt:
        continue
    tid = cat_svc.resolve(tgt, fallback="")
    if not tid and not APPLY:
        tid = -1  # placeholder for dry-run count
    counts[tgt] += 1
    if len(moved[tgt]) < 5:
        moved[tgt].append(nm[:36])
    if APPLY and tid and tid > 0:
        c.put(f"products/{p['id']}", {"categories": [{"id": tid}]})

print("\n=== moves ===", flush=True)
for k, v in counts.most_common():
    print(f"  {v:4}  → {k}", flush=True)
    for ex in moved[k]:
        print(f"          · {ex}", flush=True)
print(f"\n=== total moved={sum(counts.values())} · stay in אקססוריז={len(prods)-sum(counts.values())} "
      f"({'APPLIED' if APPLY else 'dry-run'}) ===", flush=True)
