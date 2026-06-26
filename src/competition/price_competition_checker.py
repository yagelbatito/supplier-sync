"""
Price competition checker — FUTURE MODULE.
Placeholder for future implementation.

Planned features:
- Search product by name or image on Google (via Custom Search API)
- Find lower prices on other websites
- Flag products where our price is significantly higher
- Does NOT block the sync if it fails

This module is intentionally stubbed out.
It will be implemented using Google Custom Search API or another legal method.
"""
from src.core.logger import get_logger
from src.models.product import SupplierProduct

logger = get_logger(__name__)


class PriceCompetitionChecker:
    """
    Stub implementation. Always returns None (no competition data).
    Replace this with a real implementation when ready.
    """

    def __init__(self, google_api_key: str = "", search_engine_id: str = ""):
        self._api_key = google_api_key
        self._cse_id = search_engine_id
        self._enabled = bool(google_api_key and search_engine_id)

        if not self._enabled:
            logger.info("PriceCompetitionChecker: disabled (no API key configured)")

    def check(self, product: SupplierProduct) -> dict | None:
        """
        Check if there are cheaper prices elsewhere for this product.
        Returns None if disabled or on any error.
        """
        if not self._enabled:
            return None

        try:
            return self._do_check(product)
        except Exception as exc:
            logger.warning(f"Price competition check failed (non-blocking): {exc}")
            return None

    def _do_check(self, product: SupplierProduct) -> dict | None:
        # TODO: Implement using Google Custom Search API
        # Example return format:
        # {
        #     "found_lower_price": True,
        #     "lowest_price": 450.0,
        #     "lowest_price_url": "https://...",
        #     "our_price": 500.0,
        #     "difference_percent": 10.0,
        # }
        logger.debug(f"[STUB] Price check for: {product.name}")
        return None
