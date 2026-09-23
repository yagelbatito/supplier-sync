# -*- coding: utf-8 -*-
"""Product blocklist — items we never want on the store.

The store is a Jewish home-decor / Judaica shop, so Santa-Claus / Christmas
products (which some suppliers, e.g. Julian, carry) must never be uploaded. Any
product whose NAME contains a blocked keyword is skipped at creation time
(ProductService.create) and removed by remove_blocked_products.py.

Add keywords here to extend the block.
"""

BLOCKED_KEYWORDS = [
    "סנטה",        # Santa
    "קריסמס", "כריסמס", "קריסטמס", "כריסטמס",   # Christmas (Hebrew spellings)
    "חג המולד",    # Christmas (lit. "the Nativity holiday")
    "santa", "christmas", "xmas",
]


def is_blocked(name: str) -> bool:
    """True if the product name contains any blocked keyword (case-insensitive)."""
    n = (name or "").lower()
    return any(kw.lower() in n for kw in BLOCKED_KEYWORDS)
