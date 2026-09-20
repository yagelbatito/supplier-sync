# -*- coding: utf-8 -*-
"""
Avi Gifts (avigifts.co.il) supplier — public WooCommerce Store API.

Avi Gifts runs WooCommerce with the public Store API enabled, so — like Golyan —
no login/scraping is needed. Prices and images are public.

    GET /wp-json/wc/store/v1/products?per_page=100&page=N

`fetch_all()` returns normalized dicts:
    {sku, code, name, price, in_stock, images:[url], source_cats:[name], description}
Price is in shekels (the Store API returns minor units).
"""
import html
import re
import time
import warnings
from typing import Optional

import requests

from src.core.logger import get_logger

warnings.filterwarnings("ignore")
logger = get_logger("supplier.avigifts")

BASE = "https://avigifts.co.il"
STORE_API = f"{BASE}/wp-json/wc/store/v1/products"
UA = "Mozilla/5.0 (compatible; SmadarSync/1.0)"


def _price_to_shekels(prices: dict) -> float:
    try:
        minor = int(prices.get("currency_minor_unit", 2))
        return int(prices.get("price") or 0) / (10 ** minor)
    except (ValueError, TypeError):
        return 0.0


def _clean(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _normalize(raw: dict) -> Optional[dict]:
    name = _clean(raw.get("name") or "")
    if not name:
        return None
    sku = (raw.get("sku") or "").strip() or str(raw.get("id") or "")
    prices = raw.get("prices") or {}
    images = [im.get("src") for im in (raw.get("images") or []) if im.get("src")]
    source_cats = [_clean(c.get("name", "")) for c in (raw.get("categories") or []) if c.get("name")]
    desc = _clean(raw.get("description") or raw.get("short_description") or "")
    return {
        "sku": sku,
        "code": str(raw.get("id") or sku),
        "name": name,
        "price": _price_to_shekels(prices),
        "in_stock": bool(raw.get("is_in_stock", True)),
        "images": images,
        "source_cats": source_cats,
        "description": desc,
    }


def fetch_all(per_page: int = 100, max_pages: int = 0, delay: float = 0.3) -> list:
    """Fetch every product from the Avi Gifts Store API (paginated)."""
    out: list = []
    page = 1
    with requests.Session() as h:
        h.headers.update({"Accept": "application/json", "User-Agent": UA})
        while True:
            try:
                r = h.get(STORE_API, params={"per_page": per_page, "page": page},
                          timeout=40, verify=False)
                if r.status_code == 400:      # past the last page
                    break
                r.raise_for_status()
                batch = r.json()
            except Exception as exc:
                logger.warning(f"Avi Gifts page {page} failed: {exc}")
                break
            if not batch:
                break
            for raw in batch:
                p = _normalize(raw)
                if p:
                    out.append(p)
            logger.info(f"Avi Gifts: fetched page {page} ({len(batch)}) — total {len(out)}")
            if len(batch) < per_page:
                break
            page += 1
            if max_pages and page > max_pages:
                break
            time.sleep(delay)
    logger.info(f"Avi Gifts fetch finished: {len(out)} products")
    return out
