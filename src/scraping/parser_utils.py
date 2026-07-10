"""Common HTML parsing utilities shared across supplier scrapers."""
import re

from bs4 import BeautifulSoup, Tag


def safe_text(element: Tag | None, default: str = "") -> str:
    if not element:
        return default
    return element.get_text(strip=True) or default


def safe_attr(element: Tag | None, attr: str, default: str = "") -> str:
    if not element:
        return default
    return element.get(attr, default) or default


def find_price(soup: BeautifulSoup) -> float:
    """Try multiple strategies to find a price on a page."""
    # 1) itemprop="price"
    el = soup.find(attrs={"itemprop": "price"})
    if el:
        val = el.get("content") or el.get_text(strip=True)
        price = _parse_price(val)
        if price:
            return price

    # 2) Common price CSS patterns
    for selector in [
        ".price", ".product-price", ".priceNum", ".woocommerce-Price-amount",
        "[class*='price']", "[class*='Price']",
    ]:
        for el in soup.select(selector):
            price = _parse_price(el.get_text(strip=True))
            if price:
                return price

    # 3) Any element containing ₪ and a number
    for el in soup.find_all(string=lambda t: t and "₪" in t and re.search(r"\d", t)):
        price = _parse_price(str(el))
        if price:
            return price

    return 0.0


def _parse_price(text: str) -> float:
    if not text:
        return 0.0
    clean = text.replace(",", "").replace("₪", "").replace("$", "").strip()
    nums = re.findall(r"\d+(?:\.\d+)?", clean)
    if not nums:
        return 0.0
    # Return the largest number (avoid picking up quantities like "1" or "2")
    candidates = [float(n) for n in nums]
    candidates = [c for c in candidates if c > 10]  # skip tiny numbers
    return max(candidates) if candidates else 0.0


def find_images(soup: BeautifulSoup, base_url: str = "") -> list[str]:
    """Extract product image URLs from a page."""
    images = []
    seen = set()

    selectors = [
        "div.product-gallery img",
        "div.woocommerce-product-gallery img",
        "div.bigImg img",
        ".product-images img",
        "figure.woocommerce-product-gallery__wrapper img",
    ]

    for sel in selectors:
        for img in soup.select(sel):
            src = img.get("data-src") or img.get("src") or ""
            if src and src not in seen:
                seen.add(src)
                images.append(_normalize_url(src, base_url))

    # Fallback: all imgs if nothing found
    if not images:
        for img in soup.find_all("img"):
            src = img.get("data-src") or img.get("src") or ""
            if src and _looks_like_product_image(src) and src not in seen:
                seen.add(src)
                images.append(_normalize_url(src, base_url))

    return images


def _normalize_url(src: str, base_url: str) -> str:
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/") and base_url:
        return base_url.rstrip("/") + src
    return src


def _looks_like_product_image(url: str) -> bool:
    url_lower = url.lower()
    # Skip obvious non-product images
    skip = ["logo", "banner", "icon", "placeholder", "loading", "sprite", ".svg", "pixel"]
    if any(s in url_lower for s in skip):
        return False
    # Must have an image extension or CDN path
    return bool(re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", url_lower, re.IGNORECASE))


def extract_attribute(soup: BeautifulSoup, labels: list[str]) -> str:
    """Search for a labeled attribute in product spec tables."""
    for label in labels:
        el = soup.find(string=lambda t: t and label in t)
        if el:
            parent = el.parent
            nxt = parent.find_next_sibling()
            if nxt:
                val = nxt.get_text(strip=True)
                if val:
                    return val
    return ""


def parse_dimensions(text: str) -> tuple[str, str, str]:
    """Extract (width, depth, height) in cm from free text.

    Handles the Leopard-style single line
    ``מידות המוצר : רוחב 48 | עומק 58 | גובה 91 ס"מ`` (labels + numbers on one
    line, any order), as well as W/D/H aliases. Returns ('', '', '') for any
    dimension not found. Only grabs a number that appears close AFTER the label
    so it never swallows unrelated digits (prices, JS, etc.)."""
    if not text:
        return "", "", ""

    def grab(*labels: str) -> str:
        for lab in labels:
            m = re.search(lab + r"[^\d]{0,6}(\d{1,4}(?:\.\d+)?)", text)
            if m:
                return m.group(1)
        return ""

    width = grab("רוחב", "רו­חב", r"\bW\b", "width")
    depth = grab("עומק", r"\bD\b", "depth")
    height = grab("גובה", "אורך", r"\bH\b", "height")
    return width, depth, height
