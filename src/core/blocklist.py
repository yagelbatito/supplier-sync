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


def is_blocked(*texts: str) -> bool:
    """True if ANY given text (name, description, …) contains a blocked keyword
    (case-insensitive). Christmas / Santa items must be caught by name OR
    description, from any supplier."""
    blob = " ".join(t or "" for t in texts).lower()
    return any(kw.lower() in blob for kw in BLOCKED_KEYWORDS)
