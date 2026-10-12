# -*- coding: utf-8 -*-
"""Shared logic for the Home Craft (h-craft.co.il) supplier.

Home Craft is a WIX STORES site (not WooCommerce). Its storefront catalog is read
through the Wix e-commerce GraphQL endpoint, authorised with a short-lived access
token fetched fresh from the site on each run. Colour is modelled by the supplier
as SEPARATE products sharing a base name with a " - <colour>" suffix (e.g.
"Nordic Chair - אגוז" / "Nordic Chair - אלון"); we group those siblings into one
variable product with per-colour stock. Everything else is a simple product.

Target categories + owner pricing:
    כסאות      -> store כיסאות אוכל (39)   x0.90  (-10%)
    כסאות בר   -> store כיסאות בר   (42)   x0.90  (-10%)
    כורסאות    -> store כורסאות     (46)   x0.85  (-15%)
"""
import json
import re
import time
import urllib.request

ECOM_APP = "1380b703-ce81-ff05-f115-39571d94dfcd"
BASE = "https://www.h-craft.co.il"
GQL = f"{BASE}/_api/wix-ecommerce-storefront-web/api"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"

# hcraft category name -> (store category id, price multiplier)
TARGETS = {
    "כסאות": (39, 0.90),
    "כסאות בר": (42, 0.90),
    "כורסאות": (46, 0.85),
}
SKU_PREFIX = "HC"


def _req(url, data=None, headers=None, timeout=60):
    h = {"User-Agent": UA, "Referer": BASE + "/"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h,
                                 method="POST" if data else "GET")
    last = None
    for _ in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as exc:
            last = exc
            time.sleep(3)
    raise last


def get_token():
    """Fresh storefront access token for the ecom app (tokens are short-lived)."""
    at = _req(f"{BASE}/_api/v1/access-tokens")
    return at["apps"][ECOM_APP]["instance"]


def gql(token, query, variables=None):
    body = {"query": query}
    if variables:
        body["variables"] = variables
    d = _req(GQL, data=json.dumps(body).encode(),
             headers={"Content-Type": "application/json", "Authorization": token})
    if d.get("errors"):
        raise RuntimeError("GraphQL: " + json.dumps(d["errors"], ensure_ascii=False)[:300])
    return d["data"]


def categories(token):
    d = gql(token, "{catalog{categories(limit:200,offset:0){list{id name}}}}")
    return {c["name"]: c["id"] for c in d["catalog"]["categories"]["list"]}


_PROD_Q = """query cat($cid:String!,$o:Int!,$l:Int!){
 catalog{category(categoryId:$cid){productsWithMetaData(offset:$o,limit:$l,onlyVisible:true){
  totalCount
  list{ id name urlPart sku price comparePrice discountedPrice
    isInStock ribbon
    options{ title optionType selections{ id value description inStock } }
    productItems{ id optionsSelections isVisible price discountedPrice inventory{status} }
    media{ url fullUrl mediaType }
    description
    productDimensions{ length width height unit }
  }}}}}"""


def fetch_category(token, cid):
    out, offset = [], 0
    while True:
        meta = gql(token, _PROD_Q, {"cid": cid, "o": offset, "l": 100}
                   )["catalog"]["category"]["productsWithMetaData"]
        out.extend(meta["list"])
        if not meta["list"] or len(out) >= meta["totalCount"]:
            break
        offset += 100
    return out


def current_price(p):
    """Supplier's live selling price (respect an active discount)."""
    dp = p.get("discountedPrice") or 0
    pr = p.get("price") or 0
    cand = [v for v in (dp, pr) if v]
    return min(cand) if cand else 0


def hi_res(media_item, size=1200):
    """Wix media -> a web-sized image URL."""
    full = media_item.get("fullUrl") or ""
    if full:
        return re.sub(r"/w_\d+,h_\d+", f"/w_{size},h_{size}", full)
    mid = media_item.get("url") or ""
    ext = "png" if mid.lower().endswith(".png") else "jpg"
    return f"https://static.wixstatic.com/media/{mid}/v1/fit/w_{size},h_{size},q_90/file.{ext}"


def image_urls(p, limit=8):
    return [hi_res(m) for m in (p.get("media") or []) if m.get("mediaType") == "PHOTO"][:limit]


def desc_text(description):
    """Flatten a Wix rich-content description to plain text."""
    if not description:
        return ""
    try:
        doc = json.loads(description) if isinstance(description, str) else description
    except Exception:
        return str(description)[:1000]
    out = []

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "TEXT":
                out.append(n.get("textData", {}).get("text", ""))
            for v in n.get("nodes", []) or []:
                walk(v)
    for n in doc.get("nodes", []) or []:
        walk(n)
    return " ".join(t for t in out if t).strip()


_COLOR_SPLIT = re.compile(r"\s*[-–]\s*")


def base_and_color(name):
    """('Nordic Chair - אגוז') -> ('Nordic Chair', 'אגוז'). No suffix -> (name, '')."""
    parts = _COLOR_SPLIT.split(name.strip())
    if len(parts) >= 2 and len(parts[-1]) <= 20:
        color = parts[-1].strip()
        base = _COLOR_SPLIT.split(name.strip())[0].strip()
        base = " - ".join(parts[:-1]).strip()
        return base, color
    return name.strip(), ""


def group_products(products):
    """Group colour-sibling products into variable groups.

    Returns list of dicts: {base, members:[product,...], colors:[...], variable:bool}.
    A group is variable only when >=2 DISTINCT colours exist among siblings.
    """
    buckets = {}
    order = []
    for p in products:
        base, color = base_and_color(p["name"])
        key = base.lower()
        if key not in buckets:
            buckets[key] = {"base": base, "members": [], "colors": []}
            order.append(key)
        buckets[key]["members"].append((color, p))
    groups = []
    for key in order:
        b = buckets[key]
        colors = [c for c, _ in b["members"] if c]
        distinct = list(dict.fromkeys(colors))
        variable = len(distinct) >= 2 and len(distinct) == len([c for c, _ in b["members"] if c])
        groups.append({"base": b["base"], "members": b["members"],
                       "colors": distinct, "variable": variable})
    return groups
