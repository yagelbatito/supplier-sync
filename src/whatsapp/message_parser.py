"""
Parse the user's text replies in WhatsApp into a structured command.

Expected formats (all flexible — order doesn't matter, Hebrew or English keys,
colon optional, mixed RTL/LTR is fine):

    ספק:perlahome מחיר:150
    ספק perlahome מחיר 150
    מחיר:150 ספק:perlahome
    supplier:perlahome price:150 sku:ABC123

    Additionally accepts overrides:
    מקט:ABC123      (override the OCR-extracted SKU)
    קטגוריה:סלון    (override category hint)
    שם:ספה כחולה   (override the OCR-extracted name)
    מידות:60x40x90  (override dimensions)

Why a custom parser and not just GPT?
- Latency: this runs on every reply. We don't want a 2s LLM call per message.
- Determinism: a single regex fails loudly; an LLM might silently swap "ספק" and "מחיר".
- Cost: free.
"""
import re
from dataclasses import dataclass
from typing import Optional

# Hebrew → English key normalization.
# Multiple Hebrew variants map to the same canonical key, so users can type
# whatever feels natural ("מק"ט", "מקט", "מק\"ט" all → sku).
_KEY_ALIASES = {
    # supplier
    "ספק": "supplier",
    "supplier": "supplier",
    # price
    "מחיר": "price",
    "price": "price",
    # sku
    "מקט": "sku",
    'מק"ט': "sku",
    "מק״ט": "sku",
    "sku": "sku",
    # name
    "שם": "name",
    "name": "name",
    # category
    "קטגוריה": "category",
    "category": "category",
    # dimensions
    "מידות": "dimensions",
    "מידה": "dimensions",
    "dimensions": "dimensions",
    "size": "dimensions",
    "רוחב": "width",
    "width": "width",
    "גובה": "height",
    "height": "height",
    "עומק": "depth",
    "depth": "depth",
    "אורך": "depth",
    "length": "depth",
    # special commands
    "ביטול": "cancel",
    "מחק": "cancel",
    "cancel": "cancel",
}


@dataclass
class ParsedCommand:
    """Result of parsing a text message reply."""
    supplier: Optional[str] = None
    price: Optional[float] = None
    sku_override: Optional[str] = None
    name_override: Optional[str] = None
    category_override: Optional[str] = None
    dimensions_override: Optional[str] = None
    width_override: Optional[str] = None
    height_override: Optional[str] = None
    depth_override: Optional[str] = None
    is_cancel: bool = False
    # The raw text that was parsed, useful for error messages back to user.
    raw: str = ""

    def is_complete(self) -> bool:
        """Complete when either supplier flow or manual WhatsApp flow has enough data."""
        has_price = self.price is not None and self.price > 0
        if self.supplier:
            return has_price
        return has_price and bool(self.name_override) and bool(self.category_override)

    def has_manual_product_details(self) -> bool:
        return bool(self.name_override) and self.price is not None and self.price > 0

    def missing_fields(self) -> list[str]:
        missing = []
        if not self.supplier and not (self.name_override and self.category_override):
            missing.append("ספק")
        if self.price is None:
            missing.append("מחיר")
        if not self.supplier and not self.name_override:
            missing.append("שם")
        if not self.supplier and not self.category_override:
            missing.append("קטגוריה")
        return missing


# Tokenize on whitespace, but understand that values can be multi-word
# (e.g. שם:ספה גדולה כחולה). The strategy: scan left-to-right, when we hit a
# known key, everything until the next known key is the value.

_KEY_PATTERN = re.compile(
    # Match a key followed by optional ':' or '=' and then whitespace.
    # Hebrew word boundaries are tricky — we use lookahead/lookbehind to
    # ensure we're at a "word" boundary, treating spaces and start/end as such.
    r"(?:(?<=\s)|^)(" + "|".join(re.escape(k) for k in _KEY_ALIASES.keys()) + r")\s*[:=]?\s*",
    flags=re.IGNORECASE,
)


def parse_command(text: str) -> ParsedCommand:
    """Parse a free-form text into a ParsedCommand.

    Empty or unrecognized inputs return an empty ParsedCommand (caller should
    check is_complete() before acting on it).
    """
    result = ParsedCommand(raw=text)
    if not text or not text.strip():
        return result

    cleaned = text.strip()

    # Find all (key, position) tuples.
    matches = list(_KEY_PATTERN.finditer(cleaned))
    if not matches:
        # No recognized key — maybe it's just a price as a bare number?
        # We treat "150" as "מחיר:150" for convenience.
        if re.fullmatch(r"\s*\d+(?:\.\d+)?\s*₪?\s*", cleaned):
            result.price = _parse_number(cleaned)
        return result

    # For each match, the value is everything from end-of-match until the next match's start.
    for i, m in enumerate(matches):
        canonical = _KEY_ALIASES[m.group(1).lower()]
        value_start = m.end()
        value_end = matches[i + 1].start() if i + 1 < len(matches) else len(cleaned)
        raw_value = cleaned[value_start:value_end].strip()

        if canonical == "cancel":
            result.is_cancel = True
            continue
        if canonical == "supplier":
            # Normalize to lowercase, strip quotes/punctuation that users add.
            result.supplier = raw_value.lower().strip(" \"'.,;")
        elif canonical == "price":
            num = _parse_number(raw_value)
            if num is not None:
                result.price = num
        elif canonical == "sku":
            result.sku_override = raw_value.strip(" \"'.,;")
        elif canonical == "name":
            result.name_override = raw_value
        elif canonical == "category":
            result.category_override = raw_value
        elif canonical == "dimensions":
            result.dimensions_override = raw_value
        elif canonical == "width":
            result.width_override = raw_value
        elif canonical == "height":
            result.height_override = raw_value
        elif canonical == "depth":
            result.depth_override = raw_value

    return result


def _parse_number(text: str) -> Optional[float]:
    """Pull the first number out of a string. Tolerates ₪, commas, spaces."""
    if not text:
        return None
    # Strip currency symbols and thousands separators.
    cleaned = text.replace(",", "").replace("₪", "").replace("שח", "").replace("ש\"ח", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None
