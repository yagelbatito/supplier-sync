"""
site_audit.py — independent, read-only audit of products on the LIVE site.

For each supplier it pulls every product managed by our sync (meta
_sync_managed=true and _supplier_name=<key>) straight from the WooCommerce API
and checks that each PUBLISHED product really has: image, price, description,
tags and a category. Prints a per-supplier completeness summary and lists the
offending products. Writes data/reports/audit_<ts>.json.

Read-only: it never writes to the store.
"""
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

from src.core.config_loader import load_app_settings, load_suppliers
from src.core.constants import META_SYNC_MANAGED, META_SUPPLIER_NAME
from src.woocommerce.client import WooCommerceClient

FALLBACK_CAT = "בדיקה ידנית"


def audit_product(p):
    problems = []
    if not (p.get("images") or []):
        problems.append("no-image")
    try:
        price = float(p.get("regular_price") or p.get("price") or 0)
    except (TypeError, ValueError):
        price = 0
    if price <= 0:
        problems.append("no-price")
    desc = (p.get("description") or "").strip()
    short = (p.get("short_description") or "").strip()
    if len(desc) < 20 and len(short) < 20:
        problems.append("no-description")
    if not (p.get("tags") or []):
        problems.append("no-tags")
    cats = [c.get("name", "") for c in (p.get("categories") or [])]
    if not cats:
        problems.append("no-category")
    return problems, cats


def main():
    keys = sys.argv[1:] or None
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl,
                          dry_run=True)
    suppliers = load_suppliers()
    if not keys:
        keys = [k for k, v in suppliers.items() if v.enabled]

    print(f"Pulling all products from {s.woocommerce_url} ...")
    all_products = c.get_all("products", status="any")
    print(f"Total products on store: {len(all_products)}")

    report = {"ts": datetime.now(timezone.utc).isoformat(), "suppliers": {}}

    for key in keys:
        # META_SUPPLIER_NAME stores the display name (e.g. "Perla Home"), so
        # accept either the key or the configured display name.
        accept = {key.lower(), suppliers[key].supplier_name.lower()}
        managed = []
        for p in all_products:
            meta = {m["key"]: m["value"] for m in p.get("meta_data", [])}
            if (str(meta.get(META_SYNC_MANAGED, "")).lower() in ("true", "1")
                    and meta.get(META_SUPPLIER_NAME, "").lower() in accept):
                managed.append(p)

        published = [p for p in managed if p.get("status") == "publish"]
        drafts = [p for p in managed if p.get("status") == "draft"]
        bad = []
        fallback_only = 0
        for p in published:
            problems, cats = audit_product(p)
            if problems:
                bad.append({"id": p["id"], "sku": p.get("sku"),
                            "name": (p.get("name") or "")[:50], "problems": problems})
            elif cats and all(n == FALLBACK_CAT for n in cats):
                fallback_only += 1

        clean = len(published) - len(bad)
        print(f"\n[{key}] managed={len(managed)} published={len(published)} "
              f"draft={len(drafts)} | clean={clean} bad={len(bad)} "
              f"fallback-category-only={fallback_only}")
        for b in bad[:25]:
            print(f"    ⚠️  {b['sku']} (ID {b['id']}) {b['name']}: {', '.join(b['problems'])}")
        if len(bad) > 25:
            print(f"    ... and {len(bad) - 25} more")

        report["suppliers"][key] = {
            "managed": len(managed), "published": len(published), "draft": len(drafts),
            "clean": clean, "bad": len(bad), "fallback_only": fallback_only,
            "bad_products": bad,
        }

    out = Path(__file__).resolve().parent / "data" / "reports" / \
        f"audit_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nAudit report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
