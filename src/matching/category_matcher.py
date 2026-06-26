"""
Category matching pipeline:
  1. Direct lookup in category_mapping.json per supplier
  2. Keyword matching in product name/description
  3. Fuzzy matching against WooCommerce categories
  4. Fallback to default_category
"""
import re

from thefuzz import process as fuzz

from src.core.constants import DEFAULT_REVIEW_CATEGORY
from src.core.logger import get_logger

logger = get_logger(__name__)

FUZZY_THRESHOLD = 70  # minimum score to accept a fuzzy match
WALL_ART_CATEGORY = "תמונות קיר"

_WALL_ART_TERMS = (
    "תמונה",
    "תמונות",
    "תמונת קיר",
    "ציור",
    "ציורים",
    "קנבס",
    "קנווס",
    "canvas",
    "wall art",
    "poster",
    "print",
    "הדפס",
    "אומנות",
    "אמנות",
    "אומנות ישראלית",
    "אמנות ישראלית",
)

_FLORALIS_ARTIST_SUFFIX = re.compile(r"\s[-–]\s*[\u0590-\u05ff'\"\s]{5,}$")


class CategoryMatcher:
    def __init__(
        self,
        category_mapping: dict[str, dict[str, str]],
        keyword_rules: dict[str, list[str]],
        wc_categories: list[str],
    ):
        self._mapping = category_mapping         # {supplier_key: {src_cat: our_cat}}
        self._keyword_rules = keyword_rules      # {our_cat: [keyword, ...]}
        self._wc_categories = wc_categories      # actual category names in WooCommerce

    def match(
        self,
        supplier_key: str,
        supplier_category: str,
        product_name: str,
        product_description: str = "",
        default_category: str = DEFAULT_REVIEW_CATEGORY,
    ) -> str:
        """Return the best matching WooCommerce category name."""
        if self._is_floralis_wall_art(supplier_key, supplier_category, product_name, product_description):
            logger.debug(
                f"[{supplier_key}] Floralis wall-art guard: "
                f"'{supplier_category}' / '{product_name[:40]}' → '{WALL_ART_CATEGORY}'"
            )
            return WALL_ART_CATEGORY

        # ── Step 1: Direct mapping ────────────────────────────────
        supplier_map = self._mapping.get(supplier_key, {})
        if supplier_category and supplier_category in supplier_map:
            mapped = supplier_map[supplier_category]
            if mapped != "בדיקה לפי מילות מפתח":
                logger.debug(f"[{supplier_key}] Direct map: '{supplier_category}' → '{mapped}'")
                return mapped

        # ── Step 2: Keyword matching ──────────────────────────────
        # Find the LONGEST matching keyword across all categories.
        # Longest wins so specific phrases ("שולחן סלון") beat shorter
        # substrings ("סלון") regardless of dict order.
        text = f"{supplier_category} {product_name} {product_description}".lower()
        best_match: tuple[str, str] | None = None  # (keyword, category)
        for our_cat, keywords in self._keyword_rules.items():
            for kw in keywords:
                if not kw:
                    continue
                if self._contains_keyword(text, kw):
                    if best_match is None or len(kw) > len(best_match[0]):
                        best_match = (kw, our_cat)
        if best_match:
            kw, our_cat = best_match
            logger.debug(f"[{supplier_key}] Keyword match: '{kw}' → '{our_cat}'")
            return our_cat

        # ── Step 3: Fuzzy match supplier category against our categories ─
        if supplier_category and self._wc_categories:
            result = fuzz.extractOne(supplier_category, self._wc_categories)
            if result and result[1] >= FUZZY_THRESHOLD:
                logger.debug(
                    f"[{supplier_key}] Fuzzy match: '{supplier_category}' → '{result[0]}' "
                    f"(score={result[1]})"
                )
                return result[0]

        # ── Step 4: Fallback ──────────────────────────────────────
        logger.info(
            f"[{supplier_key}] No category match for '{supplier_category}' / '{product_name[:40]}' "
            f"→ fallback '{default_category}'"
        )
        return default_category

    @staticmethod
    def _is_floralis_wall_art(
        supplier_key: str,
        supplier_category: str,
        product_name: str,
        product_description: str,
    ) -> bool:
        """Floralis sometimes gives artwork a misleading product type.

        When the title/description clearly indicates a wall-art product, keep it
        out of chair categories even if the supplier category says otherwise.
        """
        if supplier_key != "floralis":
            return False

        supplier_category_l = (supplier_category or "").lower()
        product_name_l = (product_name or "").lower()
        product_description_l = (product_description or "").lower()
        text = f"{supplier_category_l} {product_name_l} {product_description_l}"

        if any(term.lower() in text for term in _WALL_ART_TERMS):
            return True

        # Many Floralis artwork titles are formatted as "Artwork title - Artist Name".
        return bool(_FLORALIS_ARTIST_SUFFIX.search(product_name or ""))

    @staticmethod
    def _contains_keyword(text: str, keyword: str) -> bool:
        """Match keywords, guarding only bar-chair phrases from prefixes."""
        kw = keyword.lower().strip()
        if not kw:
            return False
        if kw in {"כיסא בר", "כסא בר", "bar chair", "bar stool", "בר"}:
            pattern = rf"(?<![\w\u0590-\u05ff]){re.escape(kw)}(?![\w\u0590-\u05ff])"
            return re.search(pattern, text, flags=re.IGNORECASE) is not None
        return kw in text
