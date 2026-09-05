# -*- coding: utf-8 -*-
"""
Fix Floralis wall-art wrongly sitting in 'כיסאות בר':
  • re-fetch the CORRECT title from floralis.co.il (by shopify id in the SKU)
  • move → 'תמונות קיר' (console → 'קונסולות ושידות כניסה')
  • append the corrected names to keyword_category_rules.json so a future
    re-evaluation routes them right.
Dry-run unless --apply.
"""
import sys, io, json, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
import warnings; warnings.filterwarnings("ignore")
from dotenv import load_dotenv; load_dotenv()
import httpx
from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient
from src.woocommerce.category_service import CategoryService

APPLY = "--apply" in sys.argv
s = load_app_settings()
c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                      consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                      wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=False)
cat_svc = CategoryService(c); cat_svc.load()
BAR = cat_svc.resolve("כיסאות בר", fallback="")
ART = cat_svc.resolve("תמונות קיר", fallback="")
CONS = cat_svc.resolve("קונסולות ושידות כניסה", fallback="")
STANDS = cat_svc.resolve("בתי עציץ", fallback="")
CHAIR = ("כיסא", "כסא", "בר", "סטול", "stool", "chair", "כורסא", "שרפרף")

# 1. Floralis live titles by shopify id
print("fetching floralis titles…", flush=True)
flo = {}
with httpx.Client(timeout=30, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"}) as h:
    page = 1
    while True:
        try:
            data = h.get(f"https://www.floralis.co.il/products.json?limit=250&page={page}").json()
        except Exception as e:
            print("  floralis fetch failed:", e, flush=True); break
        prods = data.get("products", [])
        if not prods:
            break
        for p in prods:
            flo[str(p.get("id"))] = {"title": (p.get("title") or "").strip(),
                                     "type": (p.get("product_type") or "").strip()}
        if len(prods) < 250:
            break
        page += 1
print(f"floralis products: {len(flo)}", flush=True)

# 2. scan bar chairs for misfits
prods = c.get_all("products", category=str(BAR), status="any")
fixes = []          # (id, cur_name, new_name|None, target_id, target_name)
for p in prods:
    nm = (p.get("name") or "").strip()
    if any(k in nm.lower() for k in CHAIR):
        continue
    sku = (p.get("sku") or "")
    info = flo.get(sku.split("-")[-1]) if sku.startswith("FLO-FLORALIS-") else None
    title = info["title"] if (info and info["title"]) else None
    ptype = info["type"] if info else "?"
    low = nm.lower()
    if title and " - " in title:                       # "Artwork - Artist" → wall art
        fixes.append((p["id"], nm, title, ART, "תמונות קיר", ptype))
    elif ("סטנד" in nm) or ("stand" in low):           # a stand, not art
        fixes.append((p["id"], nm, title, STANDS, "בתי עציץ", ptype))
    elif ("קונסולה" in nm) or ("קונזולה" in nm):
        fixes.append((p["id"], nm, None, CONS, "קונסולות ושידות כניסה", ptype))
    else:
        print(f"  ?? UNKNOWN misfit id={p['id']} «{nm}» sku={sku} type={ptype}", flush=True)

print(f"\n=== FIX PLAN ({'APPLY' if APPLY else 'DRY-RUN'}): {len(fixes)} products ===", flush=True)
for pid, cur, new, cid, cname, ptype in fixes:
    print(f"  id={pid}  «{cur[:26]}» → name:«{(new or '(keep)')[:30]}»  cat:{cname}  [floralis type:{ptype}]", flush=True)

if APPLY and fixes:
    moved_names = collections.defaultdict(list)
    for pid, cur, new, cid, cname, ptype in fixes:
        payload = {"categories": [{"id": cid}]}
        if new and new != cur:
            payload["name"] = new
        c.put(f"products/{pid}", payload)
        moved_names[cname].append(new or cur)
    # 3. persist names into the mapping
    path = "config/keyword_category_rules.json"
    rules = json.load(open(path, encoding="utf-8"))
    added = 0
    for cname, names in moved_names.items():
        rules.setdefault(cname, [])
        for n in names:
            n = (n or "").strip()
            if n and n not in rules[cname]:
                rules[cname].append(n); added += 1
    json.dump(rules, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.load(open(path, encoding="utf-8"))  # validate
    print(f"\nAPPLIED. mapping +{added} names. ({sum(len(v) for v in moved_names.values())} products fixed)", flush=True)
print("=== DONE ===", flush=True)
