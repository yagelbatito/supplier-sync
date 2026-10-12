# -*- coding: utf-8 -*-
"""Home Craft (h-craft.co.il, Wix Stores) -> WooCommerce.

Imports the chairs / bar-chairs / armchairs categories. Colour-sibling products are
merged into variable products with per-colour stock; the rest are simple. Each
product's photos are sent to GPT (vision) to expand the description — the same
confident Hebrew style used for the other suppliers — with stock, dimensions and
colours folded in. Pricing: chairs & bar chairs x0.90 (-10%), armchairs x0.85 (-15%).

    python hcraft_sync.py                 # DRY-RUN summary
    python hcraft_sync.py --limit=2 --apply
    python hcraft_sync.py --apply
    python hcraft_sync.py --apply --no-ai  # skip GPT enrichment (faster)
"""
import json
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")
from dotenv import load_dotenv

load_dotenv()

import hcraft_common as H
from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient
from src.enrichment.openai_client import OpenAIClient
from src.enrichment.prompts import FOOTER_NOTE, SYSTEM_PROMPT_PRODUCT_CONTENT
from src.core.blocklist import is_blocked

APPLY = "--apply" in sys.argv
NO_AI = "--no-ai" in sys.argv
LIMIT = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--limit=")), 0)

ATTR_COLOR = "צבע"


def enrich(ai, name, category, src_text, colors, dims, imgs):
    """GPT-vision enrichment -> dict of content fields (or {} on failure)."""
    if not ai or not ai.available or not imgs:
        return {}
    facts = [f"שם מוצר (מקור): {name}", f"קטגוריה: {category}"]
    if src_text:
        facts.append(f"תיאור מהספק: {src_text[:600]}")
    if colors:
        facts.append(f"צבעים זמינים: {', '.join(colors)}")
    if dims:
        facts.append(f"מידות: {dims}")
    facts.append("התבונן גם בתמונות המצורפות ותאר את המוצר, החומרים, הסגנון והשימושים. "
                 "אל תמציא מידות/מספרים שלא ניתנו. החזר JSON בלבד.")
    content = [{"type": "text", "text": "\n".join(facts)}]
    for u in imgs[:3]:
        content.append({"type": "image_url", "image_url": {"url": u}})
    messages = [{"role": "system", "content": SYSTEM_PROMPT_PRODUCT_CONTENT},
                {"role": "user", "content": content}]
    raw = ai.chat_messages(messages, max_tokens=1200, temperature=0.7, json_mode=True)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def build_description(data, src_text, colors, dims):
    full = (data.get("full_description") or "").strip() or src_text.strip()
    extra = []
    if dims:
        extra.append(f"מידות: {dims}")
    if colors:
        extra.append("צבעים זמינים: " + ", ".join(colors))
    tail = ("\n\n" + "\n".join(extra)) if extra else ""
    body = (full + tail).strip()
    return (body + "\n\n" + FOOTER_NOTE) if body else FOOTER_NOTE


def dims_str(p):
    d = p.get("productDimensions") or {}
    vals = [d.get("length"), d.get("width"), d.get("height")]
    if any(vals):
        u = d.get("unit") or "cm"
        return " x ".join(str(v) for v in vals if v) + f" {u}"
    # try to pull a measurement from the description text
    m = re.search(r"\d{2,3}\s*[*xX×]\s*\d{2,3}(?:\s*[*xX×]\s*\d{2,3})?", H.desc_text(p.get("description")))
    return m.group(0) if m else ""


def price_of(p, mult):
    return int(round(H.current_price(p) * mult))


def main():
    s = load_app_settings()
    wc = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                           consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                           wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=not APPLY)
    ai = None if NO_AI else OpenAIClient(os.getenv("OPENAI_API_KEY", ""),
                                         model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

    tok = H.get_token()
    cats = H.categories(tok)

    existing = set()
    if APPLY:
        page = 1
        while True:
            b = None
            for _ in range(6):
                b = wc.get("products", params={"per_page": 100, "page": page,
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

    stats = {"variable": 0, "simple": 0, "exists": 0, "created": 0, "failed": 0,
             "blocked": 0, "enriched": 0}
    done = 0
    for cat_name, (store_cat, mult) in H.TARGETS.items():
        prods = H.fetch_category(tok, cats[cat_name])
        groups = H.group_products(prods)
        print(f"\n--- {cat_name}: {len(prods)} products, {len(groups)} groups (x{mult} -> cat {store_cat})", flush=True)
        for g in groups:
            rep_color, rep = g["members"][0]
            name = g["base"] if g["variable"] else rep["name"]
            sku = f"{H.SKU_PREFIX}-{rep['id']}"
            src_text = H.desc_text(rep.get("description"))
            dims = dims_str(rep)
            colors = g["colors"]

            if is_blocked(name, src_text):
                stats["blocked"] += 1
                continue

            stats["variable" if g["variable"] else "simple"] += 1
            if not APPLY:
                done += 1
                tag = f"VAR[{','.join(colors)}]" if g["variable"] else "simple"
                print(f"  {tag} {sku} {name} -> {price_of(rep,mult)}₪ inStock={rep['isInStock']}")
                if LIMIT and done >= LIMIT:
                    print("\n(dry-run limit)"); _summary(stats); return
                continue

            if sku in existing:
                stats["exists"] += 1
                continue

            # gather images (union across colour siblings so every colour shows)
            imgs = []
            for _c, m in g["members"]:
                for u in H.image_urls(m):
                    if u not in imgs:
                        imgs.append(u)
            imgs = imgs[:10]

            data = enrich(ai, name, cat_name, src_text, colors, dims, imgs) if not NO_AI else {}
            if data:
                stats["enriched"] += 1
            improved = (data.get("improved_name") or "").strip() or name
            description = build_description(data, src_text, colors, dims)
            short = (data.get("short_description") or "").strip()
            payload = {
                "name": improved, "sku": sku, "status": "publish",
                "description": description, "short_description": short,
                "images": [{"src": u} for u in imgs[:8]],
                "categories": [{"id": store_cat}],
            }
            if data.get("seo_title"):
                payload.setdefault("meta_data", []).append({"key": "_yoast_wpseo_title", "value": data["seo_title"][:60]})
            if data.get("meta_description") or data.get("seo_meta_description"):
                md = (data.get("meta_description") or data.get("seo_meta_description"))[:155]
                payload.setdefault("meta_data", []).append({"key": "_yoast_wpseo_metadesc", "value": md})
            # product tags from the GPT enrichment (string or list)
            raw_tags = data.get("tags")
            tags = []
            if isinstance(raw_tags, str):
                tags = [t.strip() for t in raw_tags.split(",") if t.strip()][:8]
            elif isinstance(raw_tags, list):
                tags = [str(t).strip() for t in raw_tags if t][:8]
            if tags:
                payload["tags"] = [{"name": t} for t in tags]

            try:
                if g["variable"]:
                    payload["type"] = "variable"
                    payload["attributes"] = [{"name": ATTR_COLOR, "visible": True,
                                              "variation": True, "options": colors}]
                    parent = wc.post("products", payload)
                    pid = parent.get("id")
                    for color, m in g["members"]:
                        if not color:
                            continue
                        wc.post(f"products/{pid}/variations", {
                            "regular_price": str(price_of(m, mult)),
                            "attributes": [{"name": ATTR_COLOR, "option": color}],
                            "manage_stock": False,
                            "stock_status": "instock" if m.get("isInStock") else "outofstock",
                        })
                else:
                    payload["type"] = "simple"
                    payload["regular_price"] = str(price_of(rep, mult))
                    payload["manage_stock"] = False
                    payload["stock_status"] = "instock" if rep.get("isInStock") else "outofstock"
                    wc.post("products", payload)
                stats["created"] += 1
                done += 1
                print(f"  +created {sku} {improved}", flush=True)
            except Exception as exc:
                stats["failed"] += 1
                print(f"  FAIL {sku}: {str(exc)[:90]}", flush=True)
            if LIMIT and done >= LIMIT:
                _summary(stats); return
    _summary(stats)


def _summary(stats):
    print(f"\n=== Home Craft sync {'APPLIED' if APPLY else 'DRY-RUN'} ===", flush=True)
    print(stats, flush=True)


if __name__ == "__main__":
    main()
