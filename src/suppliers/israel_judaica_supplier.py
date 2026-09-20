# -*- coding: utf-8 -*-
"""
ART Judaica (israel-judaica.com) supplier — B2B fetch.

Prices are visible only to a logged-in B2B account, so we log in (Joomla /
BT-Login, no captcha) and call the site's own product API:

    GET index.php?option=com_art&task=category.getProducts&code=<cat>

which returns every product as JSON keyed by SKU, with name, B2B price and an
image filename. Images live under /big/<file> behind hot-link protection, so
they must be downloaded WITH the logged-in session (see download_image); a bare
request is redirected to the host's abuse page.

`login()` returns an authenticated requests.Session.
`fetch_all(session)` returns normalized dicts:
    {sku, code, name, price, product_code, image_url, images, in_stock}
Price is the B2B price in shekels (multiply downstream: owner wants ×1.8).
"""
import html
import re
import time
import warnings
from typing import Optional

import requests

from src.core.logger import get_logger

warnings.filterwarnings("ignore")
logger = get_logger("supplier.israel_judaica")

BASE = "https://www.israel-judaica.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
# code=1116 is the top catalog node — one call returns the whole catalogue.
CATALOG_CODE = "1116"
IMAGE_DIR = f"{BASE}/big/"          # product image directory (needs session)
REFERER = f"{BASE}/index.php?option=com_art&view=category&code={CATALOG_CODE}&lang=he"


def _logged_in_marker(text: str, user: str) -> bool:
    low = text.lower()
    return ("logout" in low or "התנתק" in text or "bttask=logout" in low
            or user.split("@")[0].lower() in low)


def login(user: str, password: str, attempts: int = 3) -> Optional[requests.Session]:
    """Log in to the B2B site and return an authenticated session (or None).
    Verification is a light homepage marker (fetching the full catalogue just to
    check would hammer the site); the DRIVER separately sanity-checks that real
    B2B prices came back. Retries with a fresh CSRF token — login is flaky."""
    for attempt in range(1, attempts + 1):
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "he,en;q=0.8", "Referer": REFERER})
        try:
            r = s.get(f"{BASE}/index.php?lang=he", timeout=40, verify=False)
            m = re.search(r'<form[^>]*btl-formlogin.*?</form>', r.text, re.S)
            if not m:
                logger.error("ART login form not found")
                time.sleep(3); continue
            fields = {}
            for inp in re.findall(r'<input[^>]*>', m.group(0)):
                n = re.search(r'name="([^"]+)"', inp)
                v = re.search(r'value="([^"]*)"', inp)
                if n:
                    fields[n.group(1)] = v.group(1) if v else ""
            # Joomla login via com_users/user.login; BT-Login supplies the CSRF
            # token + return field already present in the form.
            fields.update({"username": user, "password": password,
                           "option": "com_users", "task": "user.login"})
            chk = s.post(f"{BASE}/index.php", data=fields, timeout=40, verify=False,
                         allow_redirects=True)
            if _logged_in_marker(chk.text, user):
                logger.info(f"ART Judaica: logged in (attempt {attempt})")
                return s
            logger.warning(f"ART login attempt {attempt}: no logged-in marker, retrying")
            time.sleep(4)
        except Exception as exc:
            logger.warning(f"ART login attempt {attempt} error: {exc}")
            time.sleep(4)
    logger.error("ART login failed after retries")
    return None


def _clean(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _get_products(session: requests.Session, code: str) -> dict:
    import json as _json
    r = session.get(f"{BASE}/index.php",
                    params={"option": "com_art", "task": "category.getProducts",
                            "code": code, "lang": "he"},
                    timeout=90, verify=False)
    try:
        data = _json.loads(r.text)
    except Exception as exc:
        logger.warning(f"getProducts(code={code}) parse failed: {exc}")
        return {}
    if not data.get("status"):
        return {}
    return data.get("products") or {}


def _normalize(sku: str, raw: dict) -> Optional[dict]:
    name = _clean(raw.get("name_he") or "")
    if not name:
        return None
    try:
        price = float(raw.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    img_file = (raw.get("image") or "").strip()
    image_url = f"{IMAGE_DIR}{img_file}" if img_file else ""
    return {
        "sku": sku,
        "code": str(raw.get("product_code") or sku),
        "name": name,
        "price": price,                       # B2B price (shekels) — ×1.8 downstream
        "product_code": str(raw.get("product_code") or ""),
        "image_url": image_url,
        "images": [image_url] if image_url else [],
        "in_stock": True,                     # API has no clear stock flag; treat as available
    }


def fetch_all(session: requests.Session, codes: Optional[list] = None) -> list:
    """Fetch every product (deduped by SKU). Defaults to the whole catalogue."""
    codes = codes or [CATALOG_CODE]
    by_sku: dict = {}
    for code in codes:
        prods = _get_products(session, code)
        for sku, raw in prods.items():
            if sku in by_sku:
                continue
            p = _normalize(sku, raw)
            if p:
                by_sku[sku] = p
        logger.info(f"ART: code {code} → {len(prods)} products (total unique {len(by_sku)})")
    logger.info(f"ART fetch finished: {len(by_sku)} products")
    return list(by_sku.values())


def download_image(session: requests.Session, url: str) -> Optional[bytes]:
    """Download a product image through the authenticated session (hot-link
    protection redirects an anonymous request to the host's abuse page)."""
    if not url:
        return None
    try:
        r = session.get(url, timeout=40, verify=False, allow_redirects=True)
        ctype = r.headers.get("Content-Type", "")
        if r.status_code == 200 and ctype.startswith("image/") and r.content:
            return r.content
        logger.warning(f"image not an image ({r.status_code} {ctype}): {url}")
    except Exception as exc:
        logger.warning(f"image download failed {url}: {exc}")
    return None
