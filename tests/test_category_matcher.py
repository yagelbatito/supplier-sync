"""Unit tests for CategoryMatcher."""
import pytest

from src.matching.category_matcher import CategoryMatcher

MAPPING = {
    "leopard": {
        "כיסאות בר מעוצבים": "כיסאות בר",
        "ספות": "ספות",
    },
    "julian": {
        "ריהוט משלים": "בדיקה לפי מילות מפתח",
    },
}

KEYWORDS = {
    "ספות": ["ספה", "סלון"],
    "כורסאות": ["כורסא", "כורסה"],
    "כיסאות בר": ["כיסא בר", "כסא בר", "בר"],
    "שולחנות סלון": ["שולחן סלון", "שולחן קפה"],
}

WC_CATS = ["ספות", "כורסאות", "כיסאות בר", "שולחנות סלון", "פינות אוכל", "בדיקה ידנית"]


@pytest.fixture
def matcher():
    return CategoryMatcher(MAPPING, KEYWORDS, WC_CATS)


class TestDirectMapping:
    def test_leopard_direct(self, matcher):
        result = matcher.match("leopard", "כיסאות בר מעוצבים", "כיסא בר נוצות")
        assert result == "כיסאות בר"

    def test_leopard_sopot(self, matcher):
        result = matcher.match("leopard", "ספות", "ספה זוגית")
        assert result == "ספות"


class TestKeywordMatching:
    def test_keyword_in_name(self, matcher):
        result = matcher.match("julian", "ריהוט משלים", "ספה זוגית מעוצבת")
        # "ריהוט משלים" maps to "בדיקה לפי מילות מפתח" → triggers keyword
        # "ספה" in product name → should match ספות
        assert result == "ספות"

    def test_keyword_korsaa(self, matcher):
        result = matcher.match("julian", "", "כורסא מעוצבת לסלון")
        assert result == "כורסאות"

    def test_keyword_bar_chair(self, matcher):
        result = matcher.match("unknown", "", "כיסא בר שחור")
        assert result == "כיסאות בר"

    def test_bar_chair_does_not_match_brooklyn_prefix(self, matcher):
        result = matcher.match("floralis", "", "כיסא ברוקלין סימפל בלאק")
        assert result == "בדיקה ידנית"

    def test_floralis_artwork_beats_wrong_bar_category(self):
        matcher = CategoryMatcher(
            {},
            {
                "כיסאות בר": ["כיסא בר", "כסא בר", "כיסאות בר"],
                "תמונות קיר": ["תמונה", "אומנות ישראלית"],
            },
            ["כיסאות בר", "תמונות קיר", "בדיקה ידנית"],
        )

        result = matcher.match(
            "floralis",
            "כיסאות בר",
            "לימונים בצלוחית במפה פרחונית - טלי יאלונצקי",
        )

        assert result == "תמונות קיר"

    def test_floralis_artwork_supplier_category_maps_to_wall_art(self):
        matcher = CategoryMatcher(
            {},
            {
                "כיסאות בר": ["כיסא בר", "כסא בר", "כיסאות בר"],
                "תמונות קיר": ["תמונה", "אומנות ישראלית"],
            },
            ["כיסאות בר", "תמונות קיר", "בדיקה ידנית"],
        )

        result = matcher.match("floralis", "אומנות ישראלית", "How do you feel - ליאנה נבון")

        assert result == "תמונות קיר"


class TestFallback:
    def test_no_match_returns_default(self, matcher):
        result = matcher.match("unknown", "קטגוריה לא קיימת", "מוצר ללא קטגוריה")
        assert result == "בדיקה ידנית"

    def test_custom_default(self, matcher):
        result = matcher.match("unknown", "", "מוצר ללא שם ידוע", default_category="אחר")
        assert result == "אחר"
