# -*- coding: utf-8 -*-
"""
category_audit_ai.py — store-wide category audit using the LLM classifier.

Runs the per-product classifier (src/enrichment/product_classifier.py) over EVERY
managed product on the site, compares the classifier's proposal to the product's
CURRENT category, and reports why some sit in the wrong place. Read-only by
default; --apply moves the safe cases.

Flags per product:
  OK            current category == classifier's proposal
  REVIEW        currently in "בדיקה ידנית" / no category → classifier found a home
  NO-HOME       currently unclassified AND classifier also unsure (needs a human)
  MISMATCH      has a real category but the classifier proposes a different one

Outputs data/reports/category_audit_ai.csv and .json
  (id, sku, supplier, status, current, proposed, confidence, flag, reason, link)

    python category_audit_ai.py                 # report only
    python category_audit_ai.py --apply         # move REVIEW/NO-HOME cases (>=min-conf)
    python category_audit_ai.py --apply-mismatch # also move MISMATCH cases (riskier)
    python category_audit_ai.py --min-conf 0.7
"""
import csv
import json
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")
from dotenv import load_dotenv

load_dotenv()

from config.golyan_mapping import CLASSIFIER_HINTS
from src.core.config_loader import load_app_settings, load_shipping_class_mapping
from src.core.constants import META_SUPPLIER_NAME, META_SYNC_MANAGED
from src.enrichment.openai_client import OpenAIClient
from src.enrichment.product_classifier import ProductClassifier
from src.woocommerce.category_service import CategoryService
from src.woocommerce.client import WooCommerceClient

FALLBACK = "בדיקה ידנית"
APPLY = "--apply" in sys.argv
APPLY_MISMATCH = "--apply-mismatch" in sys.argv
MIN_CONF = float(sys.argv[sys.argv.index("--min-conf") + 1]) if "--min-conf" in sys.argv else 0.6


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl,
                          dry_run=not (APPLY or APPLY_MISMATCH))
    cat_svc = CategoryService(c); cat_svc.load()
    real_cats = set(cat_svc.category_names)

    # leaf categories for the classifier
    raw_cats = c.get_all("products/categories")
    by_id = {rc["id"]: rc for rc in raw_cats}
    parent_ids = {rc.get("parent") for rc in raw_cats if rc.get("parent")}
    leaves = [{"name": rc["name"],
               "parent": (by_id.get(rc.get("parent"), {}).get("name", "") if rc.get("parent") else "")}
              for rc in raw_cats if rc["id"] not in parent_ids]

    ai = OpenAIClient(api_key=s.openai_api_key, model=s.openai_model)
    clf = ProductClassifier(ai, leaves, hints=CLASSIFIER_HINTS)

    print("loading all managed products…", flush=True)
    allp = c.get_all("products", status="any")
    managed = []
    for p in allp:
        meta = {m["key"]: m["value"] for m in p.get("meta_data", [])}
        if str(meta.get(META_SYNC_MANAGED, "")).lower() in ("true", "1"):
            managed.append((p, str(meta.get(META_SUPPLIER_NAME, ""))))
    print(f"managed products: {len(managed)} / {len(allp)} total", flush=True)

    print(f"classifying {len(managed)} products…", flush=True)
    cls = clf.classify([{"sku": str(p["id"]), "name": p.get("name", "")} for p, _ in managed]) if clf.available else {}

    rows = []
    counts = {"OK": 0, "REVIEW": 0, "NO-HOME": 0, "MISMATCH": 0}
    for p, supplier in managed:
        cur = [x["name"] for x in p.get("categories", [])]
        cur_main = cur[0] if cur else ""
        r = cls.get(str(p["id"]))
        proposed = (r.category if r else "") or ""
        conf = (r.confidence if r else 0.0)
        reason = (r.reason if r else "")
        if proposed and proposed not in real_cats:
            proposed = ""

        if not cur or cur_main == FALLBACK:
            flag = "REVIEW" if proposed else "NO-HOME"
        elif proposed and proposed != cur_main:
            flag = "MISMATCH"
        else:
            flag = "OK"
        counts[flag] += 1
        rows.append({"id": p["id"], "sku": p.get("sku", ""), "supplier": supplier or "—",
                     "status": p.get("status"), "name": p.get("name", ""),
                     "current": cur_main, "proposed": proposed, "confidence": round(conf, 2),
                     "flag": flag, "reason": reason[:60], "link": p.get("permalink", "")})

    os.makedirs("data/reports", exist_ok=True)
    order = {"NO-HOME": 0, "REVIEW": 1, "MISMATCH": 2, "OK": 3}
    rows.sort(key=lambda x: (order.get(x["flag"], 9), -x["confidence"]))
    with open("data/reports/category_audit_ai.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["flag", "id", "sku", "supplier", "status", "name",
                                          "current", "proposed", "confidence", "reason", "link"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    with open("data/reports/category_audit_ai.json", "w", encoding="utf-8") as f:
        json.dump({"counts": counts, "rows": rows}, f, ensure_ascii=False)

    print("\n=== AUDIT ===", flush=True)
    for k in ("OK", "MISMATCH", "REVIEW", "NO-HOME"):
        print(f"  {k:9} {counts[k]}", flush=True)
    print("report: data/reports/category_audit_ai.csv (+ .json)", flush=True)

    if not (APPLY or APPLY_MISMATCH):
        print("\n(read-only — review the report, then run --apply)", flush=True)
        return

    # ── APPLY (safe cases) ──
    from src.woocommerce.product_service import ProductService  # noqa
    shipping_map = load_shipping_class_mapping()
    moved = 0
    for r in rows:
        if r["flag"] in ("REVIEW",) or (APPLY_MISMATCH and r["flag"] == "MISMATCH"):
            if not r["proposed"] or r["confidence"] < MIN_CONF:
                continue
            cid = cat_svc.resolve_list(r["proposed"])
            ship = shipping_map.get(r["proposed"], "")
            payload = {"categories": [{"id": x} for x in cid]}
            if ship:
                payload["shipping_class"] = ship
            try:
                c.put(f"products/{r['id']}", payload)
                moved += 1
                if moved % 50 == 0:
                    print(f"  …moved {moved}", flush=True)
            except Exception as exc:
                print(f"  fail {r['id']}: {str(exc)[:60]}", flush=True)
    print(f"\n=== APPLIED: moved {moved} products (min-conf {MIN_CONF}, "
          f"mismatch={'yes' if APPLY_MISMATCH else 'no'}) ===", flush=True)


if __name__ == "__main__":
    main()
