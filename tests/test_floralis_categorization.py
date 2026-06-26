"""End-to-end check: real config files correctly categorize Floralis products."""
import json
from pathlib import Path

import pytest

from src.matching.category_matcher import CategoryMatcher

CONFIG_DIR = Path(__file__).parent.parent / "config"


@pytest.fixture
def matcher():
    mapping = json.loads((CONFIG_DIR / "category_mapping.json").read_text(encoding="utf-8"))
    keywords = json.loads((CONFIG_DIR / "keyword_category_rules.json").read_text(encoding="utf-8"))
    # WooCommerce category names = the keyword-rule keys, plus any nested
    # category_mapping values (the file may be flat {cat: [synonyms]} or
    # nested {supplier: {src: dest}} — handle both without crashing).
    wc_cats = set(keywords.keys())
    for v in mapping.values():
        if isinstance(v, dict):
            wc_cats.update(x for x in v.values() if x != "בדיקה לפי מילות מפתח")
    return CategoryMatcher(mapping, keywords, list(wc_cats))


@pytest.mark.parametrize("supplier_cat,product_name,expected", [
    # From the WooCommerce screenshot — these were previously miscategorized as "ספות".
    ("", "שולחן סלון גלוסי קפוצ'ינו",                  "שולחנות סלון"),
    ("", "שולחן סלון קאשמיר גלוסי",                    "שולחנות סלון"),
    ("", "שולחן צד גלוסי קאשמיר",                       "שולחנות צד"),
    ("", "שולחן סלון גלוסי קאשמיר",                    "שולחנות סלון"),
    # Ambiguous: "שולחן" alone (no סלון/צד/אוכל qualifier) → manual review
    ("", "שולחן גלוסי קפוצ'ינו מעוצב",                 "בדיקה ידנית"),

    # Accessories (vases, trays, stands, baskets, frames, boxes, bottles)
    ("אגרטלים",        "בקבוק פורסט ענתיק M",          "אקססוריז"),
    ("אגרטלים",        "בקבוק פורסט ענתיק L",          "אקססוריז"),
    ("מגשים ומעמדים",  "מעמד ריזורט 39x39x20",         "אקססוריז"),
    ("מגשים ומעמדים",  "מגש ריזורט ראונד",             "אקססוריז"),
    ("",               "סל ריזורט 30x30x42",           "אקססוריז"),
    ("",               "מסגרת עץ אלגנטית בירדי S",     "אקססוריז"),
    ("",               "קופסת איחסון בירדי M",         "אקססוריז"),

    # Mirrors & lighting
    ("מראות ותאורה",   "מראה ריזורט מעוצבת",           "מראות"),
    ("",               "מנורת ריזורט עגולה מעוצבת",    "תאורה"),
    ("",               "מנורת תלייה ריזורט טבעית",     "תאורה"),
    ("",               "מראה טיימלס בעיצוב קלאסי",     "מראות"),

    # Chairs
    ("",               "כיסא בר ניקל שחור",            "כיסאות בר"),
    ("",               "כיסא אוכל מעוצב",              "כיסאות אוכל"),
    ("",               "כיסא ניקל בעיצוב מודרני",      "כיסאות אוכל"),

    # Sofas & armchairs
    ("",               "ספה זוגית קלאסית",             "ספות"),
    ("",               "כורסא מעוצבת לסלון",           "כורסאות"),

    # Regression: the literal word "סלון" alone in a sofa context shouldn't
    # accidentally route "שולחן סלון" to ספות anymore.
    ("",               "שולחן סלון מודרני",            "שולחנות סלון"),
])
def test_floralis_real_products(matcher, supplier_cat, product_name, expected):
    result = matcher.match(
        supplier_key="floralis",
        supplier_category=supplier_cat,
        product_name=product_name,
        product_description="",
        default_category="בדיקה ידנית",
    )
    assert result == expected, f"'{product_name}' → got '{result}', expected '{expected}'"
