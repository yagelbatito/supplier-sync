# -*- coding: utf-8 -*-
"""Belini (belini.co.il) chairs category -> WooCommerce, at -37% (x0.63).

Source: Belini is WooCommerce; its Store API exposes the chairs category (id 15).
Each chair model is a VARIABLE product with a color attribute ("צבע"). Belini
creates a variation only for colors it actually stocks; a color offered as an
attribute term but with NO variation is SOLD OUT. We mirror that: every offered
color becomes a variation here, in-stock colors as 'instock' and the rest as
'outofstock', so shoppers see which colors are available.

Pricing: the store category price x0.63 (owner: "פחות 37 אחוז"), rounded.
Routing: a name containing "בר" -> כיסאות בר (42), else כיסאות אוכל (39).
Naming: Belini's own model name ("כיסא דגם NNN") is kept; no supplier name.

    python belini_sync.py                 # DRY-RUN summary
    python belini_sync.py --limit=1 --apply
    python belini_sync.py --apply
"""
import sys
import time
import urllib.parse
import warnings

warnings.filterwarnings("ignore")
import requests
from dotenv import load_dotenv

load_dotenv()

from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient

STORE_API = "https://belini.co.il/wp-json/wc/store/v1"
CAT_ID = 15                      # Belini chairs category
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"
HDRS = {"User-Agent": UA, "Accept": "application/json", "Referer": "https://belini.co.il/"}
MULT = 0.63                      # -37%
CHAIR_DINING = 39                # כיסאות אוכל
CHAIR_BAR = 42                   # כיסאות בר

APPLY = "--apply" in sys.argv
LIMIT = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--limit=")), 0)

_sess = requests.Session()
_sess.headers.update(HDRS)


def api(path):
    for _ in range(6):
        try:
            r = _sess.get(STORE_API + path, timeout=40)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(3)
    return None


def fetch_chairs():
    """Return list of product dicts (full), variable ones fetched individually so
    the embedded `variations` field (the reliable per-color stock source) is set."""
    ids = []
    page = 1
    while True:
        b = api(f"/products?category={CAT_ID}&per_page=100&page={page}&_fields=id,type")
        if not b:
            break
        ids += [(p["id"], p["type"]) for p in b]
        if len(b) < 100:
            break
        page += 1
    prods = []
    for pid, typ in ids:
        p = None
        for _ in range(4):
            p = api(f"/products/{pid}")
            # for variable products insist on a populated variations field
            if p and (typ != "variable" or p.get("variations")):
                break
            time.sleep(1.5)
        if p:
            prods.append(p)
        time.sleep(0.15)
    return prods


def price_ils(prices):
    return int(prices["price"]) / (10 ** int(prices["currency_minor_unit"]))


def discounted(p):
    return int(round(p * MULT))


def route(name):
    return CHAIR_BAR if "בר" in name else CHAIR_DINING


def colors_of(p):
    """(all_color_names, set_of_in_stock_names). In-stock = has a variation."""
    slug2name = {}
    for a in p.get("attributes", []):
        for t in a.get("terms", []):
            slug2name[t["slug"]] = t["name"]
    all_colors = [t["name"] for a in p.get("attributes", []) for t in a.get("terms", [])]
    in_stock = set()
    for v in p.get("variations", []):
        for at in v.get("attributes", []):
            val = urllib.parse.unquote(at.get("value", ""))
            in_stock.add(slug2name.get(val, slug2name.get(at.get("value", ""), val)))
    return all_colors, in_stock


def main():
    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)

    chairs = fetch_chairs()
    print(f"fetched {len(chairs)} chairs from Belini", flush=True)

    # warm existing-SKU set once (store has no reliable SKU search)
    existing = set()
    if APPLY:
        page = 1
        while True:
            b = None
            for _ in range(6):
                b = c.get("products", params={"per_page": 100, "page": page,
                                              "status": "any", "_fields": "sku"})
                if b is not None:
                    break
            if not b:
                break
            existing.update(p.get("sku", "") for p in b if p.get("sku"))
            if len(b) < 100:
                break
            page += 1
        print(f"warmed SKU cache: {len(existing)} existing products", flush=True)

    stats = {"variable": 0, "simple": 0, "exists": 0, "created": 0, "failed": 0, "no_price": 0}
    done = 0
    for p in chairs:
        name = p["name"].strip()
        base = price_ils(p["prices"])
        if not base:
            stats["no_price"] += 1
            continue
        sku = f"BEL-{p['id']}"
        imgs = [{"src": im["src"]} for im in p.get("images", [])][:8]
        cat = route(name)
        is_var = p["type"] == "variable" and p.get("variations")
        stats["variable" if is_var else "simple"] += 1

        if not APPLY:
            if is_var:
                allc, ins = colors_of(p)
                oos = [x for x in allc if x not in ins]
                print(f"  VAR {sku} {name} {discounted(base)}₪ | colors={len(allc)} "
                      f"instock={len(ins)} oos={oos}")
            else:
                print(f"  SIMPLE {sku} {name} {discounted(base)}₪")
            done += 1
            if LIMIT and done >= LIMIT:
                break
            continue

        if sku in existing:
            stats["exists"] += 1
            continue

        payload = {"name": name, "sku": sku, "status": "publish",
                   "description": p.get("description") or p.get("short_description") or "",
                   "images": imgs, "categories": [{"id": cat}]}
        try:
            if is_var:
                allc, ins = colors_of(p)
                payload["type"] = "variable"
                payload["attributes"] = [{"name": "צבע", "visible": True, "variation": True,
                                          "options": allc}]
                parent = c.post("products", payload)
                pid = parent.get("id")
                price = str(discounted(base))
                for col in allc:
                    c.post(f"products/{pid}/variations", {
                        "regular_price": price,
                        "attributes": [{"name": "צבע", "option": col}],
                        "manage_stock": False,
                        "stock_status": "instock" if col in ins else "outofstock",
                    })
            else:
                payload["type"] = "simple"
                payload["regular_price"] = str(discounted(base))
                c.post("products", payload)
            stats["created"] += 1
            done += 1
            print(f"  +created {sku} {name}", flush=True)
        except Exception as exc:
            stats["failed"] += 1
            print(f"  FAIL {sku}: {str(exc)[:80]}", flush=True)
        if LIMIT and done >= LIMIT:
            break

    print(f"\n=== Belini sync {'APPLIED' if APPLY else 'DRY-RUN'} ===", flush=True)
    print(stats, flush=True)


if __name__ == "__main__":
    main()
