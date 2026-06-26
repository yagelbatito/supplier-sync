"""General utility functions."""
import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

from slugify import slugify


def stable_sku(supplier_key: str, product_id: str | None, product_url: str, prefix: str) -> str:
    """
    Build a stable, UNIQUE SKU.

    Priority:
      1. clean ASCII product_id  → prefix + supplier_key + product_id
      2. Hebrew / percent-encoded product_id (e.g. Leopard URL slugs)
         → prefix + supplier_key + short-context + sha1(url)
         A non-latin slug, once stripped to ASCII and truncated to 30 chars,
         collapses to the same dashes for every product sharing the first few
         words — which caused many distinct products to share ONE SKU and
         overwrite each other. Appending a URL hash guarantees uniqueness.
      3. no product_id → prefix + slug + sha1(url)
    """
    if product_id:
        pid = str(product_id)
        degenerate = ("%" in pid) or any(ord(ch) > 127 for ch in pid)
        if not degenerate:
            # clean ASCII id (e.g. Floralis/Perla numeric ids) — keep as-is
            safe_id = re.sub(r"[^a-zA-Z0-9\-_]", "-", pid)[:30]
            return f"{prefix}-{supplier_key}-{safe_id}".upper()[:60]
        # Hebrew / encoded slug → truncation collides → add URL hash
        h = hashlib.sha1(product_url.encode("utf-8")).hexdigest()[:10]
        context = re.sub(r"[^a-zA-Z0-9]", "", pid)[:12]  # a little human context
        if context:
            return f"{prefix}-{supplier_key}-{context}-{h}".upper()[:60]
        return f"{prefix}-{supplier_key}-{h}".upper()[:60]

    h = hashlib.sha1(product_url.encode("utf-8")).hexdigest()[:8]
    u = urlparse(product_url)
    path = unquote(u.path).strip("/")
    last = path.split("/")[-1] if path else "product"
    slug = slugify(last) or "product"
    return f"{prefix}-{slug}-{h}".upper()[:60]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_price(text: str) -> float:
    """Extract numeric price from a string like '₪ 1,234.50'."""
    if not text:
        return 0.0
    nums = re.findall(r"[\d,\.]+", text.replace(",", ""))
    for n in reversed(nums):
        try:
            return float(n)
        except ValueError:
            continue
    return 0.0


def normalize_url(src: str, base_url: str = "") -> str:
    if not src:
        return ""
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/") and base_url:
        return base_url.rstrip("/") + src
    return src


def strip_supplier_name(text: str, supplier_names: list[str]) -> str:
    """Remove supplier brand names from product text."""
    result = text
    for name in supplier_names:
        result = re.sub(re.escape(name), "", result, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", result).strip()


def truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
