# -*- coding: utf-8 -*-
"""Diagnose 'כיסאות בר': list products whose NAME has no chair/bar word — the
likely misplaced ones (wall-art etc.). Also pull the supplier source URL."""
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
BAR = cat_svc.resolve("כיסאות בר", fallback="")
CHAIR = ("כיסא", "כסא", "בר", "סטול", "stool", "chair", "כורסא", "שרפרף")

prods = c.get_all("products", category=str(BAR), status="any")
print(f"'כיסאות בר' (id={BAR}): {len(prods)} products\n", flush=True)

misfits = []
for p in prods:
    nm = (p.get("name") or "").strip()
    if not any(k in nm.lower() for k in CHAIR):
        meta = {m["key"]: m["value"] for m in p.get("meta_data", [])}
        src = meta.get("_supplier_url") or meta.get("supplier_url") or ""
        misfits.append((p["id"], p.get("sku",""), nm, src))

print(f"=== NO chair/bar word in name → likely misplaced: {len(misfits)} ===", flush=True)
for pid, sku, nm, src in misfits:
    print(f"  id={pid} [{sku}]  «{nm[:40]}»  {src[:55]}", flush=True)
