"""
WooCommerce category service.
Loads all categories and resolves names → IDs.
NEVER creates new categories automatically.
"""
from typing import Optional

from src.core.logger import get_logger
from src.woocommerce.client import WooCommerceClient

logger = get_logger(__name__)


class CategoryService:
    def __init__(self, client: WooCommerceClient):
        self._client = client
        self._categories: dict[str, int] = {}   # name → id
        self._loaded = False

    def load(self) -> None:
        """Load all categories from WooCommerce. Call once at startup."""
        if self._loaded:
            return
        cats = self._client.get_all("products/categories")
        for cat in cats:
            name = cat.get("name", "").strip()
            cat_id = cat.get("id")
            if name and cat_id:
                self._categories[name] = cat_id
        self._loaded = True
        logger.info(f"Loaded {len(self._categories)} WooCommerce categories")

    def resolve(self, category_name: str, fallback: str = "בדיקה ידנית") -> Optional[int]:
        """
        Resolve a category name to a WooCommerce ID.
        Returns the fallback category ID if name not found.
        NEVER creates new categories.
        """
        if not self._loaded:
            self.load()

        cat_id = self._categories.get(category_name)
        if cat_id:
            return cat_id

        # Try fallback
        fallback_id = self._categories.get(fallback)
        if fallback_id:
            logger.info(f"Category '{category_name}' not found → using '{fallback}' (ID={fallback_id})")
            return fallback_id

        logger.warning(f"Category '{category_name}' AND fallback '{fallback}' not found in WooCommerce")
        return None

    def resolve_list(self, category_name: str, fallback: str = "בדיקה ידנית") -> list[int]:
        cat_id = self.resolve(category_name, fallback)
        return [cat_id] if cat_id else []

    @property
    def category_names(self) -> list[str]:
        if not self._loaded:
            self.load()
        return list(self._categories.keys())
