# -*- coding: utf-8 -*-
"""Muriel Art catalog -> WooCommerce. Uploads only PRICED items (x1.8).

Source: https://muriel-art-catalog.vercel.app/data.json (public). Products with a
size table become VARIABLE products (a "מידה" attribute, each size its own price);
single-price ones become simple products. Images come from /img/v/<id>.webp;
broken/missing images are skipped (owner rule). Category routing + naming follow
the owner's rules. 'vintage' is left unmapped (skipped) until the owner maps it.

    python muriel_sync.py                 # DRY-RUN summary
    python muriel_sync.py --limit=10 --apply
    python muriel_sync.py --apply
"""
import math
import sys
import warnings

warnings.filterwarnings("ignore")
import requests
from dotenv import load_dotenv

load_dotenv()

from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient

DATA_URL = "https://muriel-art-catalog.vercel.app/data.json"
IMG_BASE = "https://muriel-art-catalog.vercel.app/img/v/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MULT = 1.8
APPLY = "--apply" in sys.argv
LIMIT = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--limit=")), 0)

CAT_ID = {
    "תמונות קיר": 826, "תמונות לייזר": 9179, "בלוקים אקרילים": 94,
    "שולחנות סלון": 45, "שולחנות צד": 87, "שולחנות אוכל": 6559,
    "קונסולות ושידות כניסה": 50, "מזנונים": 48, "מראות": 27, "אקססוריז": 26,
}
BASE_NAME = {
    "תמונות קיר": "תמונת קנבס", "תמונות לייזר": "תמונת לייזר",
    "בלוקים אקרילים": "בלוק אקרילי", "מראות": "מראת לייזר",
    "שולחנות סלון": "שולחן", "שולחנות צד": "שולחן צד", "שולחנות אוכל": "שולחן אוכל",
    "קונסולות ושידות כניסה": "קונסולה", "מזנונים": "מזנון", "אקססוריז": "מתלה בגדים",
}
FURNITURE = {"שולחנות סלון", "שולחנות צד", "שולחנות אוכל", "קונסולות ושידות כניסה", "מזנונים", "אקססוריז"}


def priceable(it):
    return it.get("price") or it.get("table")


def route(it):
    """Owner's category rules → store category name, or None to skip."""
    c = it.get("c", "")
    n = (it.get("t") or "").strip()
    if n == "מוריאל אומנות":
        n = ""
    if "לייזר סימטאות" in c or "laser/circle" in c:
        return "תמונות לייזר"
    if "laser mirror" in c:
        return "מראות"
    if "בלוקים" in c:
        return "בלוקים אקרילים"
    if any(k in c for k in ["Print/sp", "Print/Effect", "ים התיכון", "Print/סט", "שלטים פלוס מסגרת", "Print/"]):
        return "תמונות קיר"
    if "רהיטי מתכת/שולחנות" in c:
        return "שולחנות סלון"
    if "רהיטי מתכת/נשכנים" in c:
        return "שולחנות צד"
    if "רהיטי מתכת/קונסולות" in c:
        return "קונסולות ושידות כניסה"
    if "מתלה בגדים" in c:
        return "אקססוריז"
    if "נשכנים עץ ובטון" in c:
        return "שולחנות צד"
    if "בטון ופורמיקה/קונסולות" in c:
        return "קונסולות ושידות כניסה"
    if "שולחנות פורמיקה" in c:
        return "שולחנות סלון"
    if "רהיטים בטון" in c:
        if "קונסול" in n:
            return "קונסולות ושידות כניסה"
        if "סלינה" in n:
            return "שולחנות סלון"
        if "נשכן" in n:
            return "שולחנות צד"
        if "סט שולחנות" in n:
            return "שולחנות סלון"
        if "פינת אוכל" in n:
            return "שולחנות אוכל"
        if "מזנון" in n:
            return "מזנונים"
        if "שולחן" in n:
            return "שולחנות סלון"
        return None
    if "יבוא" in c.split("/"):
        return "מראות" if n else None       # named import -> mirrors, else skip
    return None                              # vintage & anything else -> skip


def gen_name(it, cat):
    real = (it.get("t") or "").strip()
    if real == "מוריאל אומנות":
        real = ""
    num = str(int(it["id"][1:]))             # unique suffix from id
    if real and cat in FURNITURE:
        return f"{real} {num}"
    return f"{BASE_NAME.get(cat, 'פריט')} {num}"


def img_ok(sess, url):
    """Owner rule (option ג): skip broken/missing images."""
    try:
        r = sess.head(url, timeout=20, allow_redirects=True)
        cl = int(r.headers.get("Content-Length", 0))
        if r.status_code == 200 and cl > 3000:
            return True
        if r.status_code == 200 and cl == 0:      # some CDNs omit length on HEAD
            g = sess.get(url, timeout=25)
            return g.status_code == 200 and len(g.content) > 3000
        return False
    except Exception:
        return False


def build_desc(it):
    parts = []
    if it.get("m"):
        parts.append(f"חומר: {it['m']}")
    dims = it.get("d") or []
    if dims:
        parts.append("מידות: " + ", ".join(str(x) for x in dims))
    return " | ".join(parts)


def main():
    data = requests.get(DATA_URL, headers={"User-Agent": UA}, timeout=90).json()
    items = data["items"]
    variants = data.get("variants", {})

    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)
    sess = requests.Session()
    sess.headers["User-Agent"] = UA

    consumed = set()
    stats = {"variable": 0, "simple": 0, "skip_novalue": 0, "skip_route": 0,
             "skip_img": 0, "exists": 0, "created": 0, "failed": 0}
    created = 0
    for it in items:
        if it["id"] in consumed:
            continue
        for vid in variants.get(it["id"], []):
            consumed.add(vid)
        if not priceable(it):
            stats["skip_novalue"] += 1
            continue
        cat = route(it)
        if not cat:
            stats["skip_route"] += 1
            continue

        # variations
        sizes = []
        for row in (it.get("table") or []):
            try:
                pr = int(math.ceil(float(row["price"]) * MULT))
            except (TypeError, ValueError, KeyError):
                continue
            if pr:
                sizes.append({"size": str(row.get("size", "")).strip(), "price": pr})
        kind = "variable" if sizes else "simple"
        base_price = min(s["price"] for s in sizes) if sizes else int(math.ceil(float(it["price"]) * MULT))
        stats[kind] += 1

        sku = "MUR-" + it["id"]
        img = IMG_BASE + it["id"] + ".webp"
        name = gen_name(it, cat)

        if not APPLY:
            if LIMIT and created >= LIMIT:
                break
            created += 1
            continue

        # skip if already created
        try:
            if c.get("products", params={"sku": sku, "_fields": "id"}):
                stats["exists"] += 1
                continue
        except Exception:
            pass
        if not img_ok(sess, img):            # owner rule ג
            stats["skip_img"] += 1
            continue

        payload = {
            "name": name, "sku": sku, "status": "publish",
            "description": build_desc(it), "images": [{"src": img}],
            "categories": [{"id": CAT_ID[cat]}],
        }
        try:
            if kind == "variable":
                payload["type"] = "variable"
                payload["attributes"] = [{"name": "מידה", "visible": True, "variation": True,
                                          "options": [s["size"] for s in sizes]}]
                parent = c.post("products", payload)
                pid = parent.get("id")
                for sz in sizes:
                    c.post(f"products/{pid}/variations",
                           {"regular_price": str(sz["price"]),
                            "attributes": [{"name": "מידה", "option": sz["size"]}]})
            else:
                payload["type"] = "simple"
                payload["regular_price"] = str(base_price)
                c.post("products", payload)
            stats["created"] += 1
            created += 1
            if stats["created"] % 50 == 0:
                print(f"  …created {stats['created']}", flush=True)
        except Exception as exc:
            stats["failed"] += 1
            print(f"  FAIL {sku}: {str(exc)[:70]}", flush=True)
        if LIMIT and created >= LIMIT:
            break

    print(f"\n=== Muriel sync {'APPLIED' if APPLY else 'DRY-RUN'} ===", flush=True)
    print(stats, flush=True)


if __name__ == "__main__":
    main()
