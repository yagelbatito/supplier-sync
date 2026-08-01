# -*- coding: utf-8 -*-
"""Analyse the אקססוריז category by product-name type, to inform a re-map proposal."""
import sys, io, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
import warnings; warnings.filterwarnings("ignore")
from dotenv import load_dotenv; load_dotenv()
from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient
from src.woocommerce.category_service import CategoryService

s = load_app_settings()
c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                      consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                      wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=True)
cat_svc = CategoryService(c); cat_svc.load()
ACC = cat_svc.resolve("אקססוריז", fallback="")
prods = c.get_all("products", category=str(ACC), status="any")
print(f"אקססוריז total: {len(prods)}", flush=True)

def bucket(n):
    if "מברשת" in n: return "מברשות (שירותים)"
    if "פח" in n:
        if any(x in n for x in ("שירות", "אמבט", "רחצ")): return "פחי אשפה - שירותים"
        return "פחי אשפה - מטבח"
    if "עציץ" in n or "פלנטר" in n or "בית עציץ" in n: return "בתי עציץ / פלנטרים"
    if "אגרטל" in n or "ואזה" in n or "vase" in n.lower(): return "אגרטלים / ואזות"
    if "קרמיקה" in n and ("כד" in n): return "כדים מקרמיקה"
    if n.startswith("כד") or "כדים" in n or " כד " in n: return "כדים"
    if "פסל" in n: return "פסלים"
    if "שעון" in n: return "שעונים"
    return "אחר (לא מסווג)"

counts = collections.Counter()
examples = collections.defaultdict(list)
for p in prods:
    nm = (p.get("name") or "").strip()
    b = bucket(nm)
    counts[b] += 1
    if len(examples[b]) < 4:
        examples[b].append(nm[:34])

print("\n=== breakdown ===", flush=True)
for b, n in counts.most_common():
    print(f"  {n:4}  {b}", flush=True)
    for ex in examples[b]:
        print(f"          · {ex}", flush=True)
