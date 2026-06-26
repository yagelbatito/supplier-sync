"""
Checks whether a SupplierProduct already exists in WooCommerce.
Lookup order:
  1. By SKU (most reliable)
  2. By supplier_url in meta_data
"""
from src.core.constants import META_SUPPLIER_URL, META_SYNC_MANAGED
from src.core.logger import get_logger
from src.models.product import SupplierProduct

logger = get_logger(__name__)


class ProductMatcher:
    def __init__(self, product_service) -> None:
        """product_service is src.woocommerce.product_service.ProductService"""
        self._svc = product_service

    def find_existing(self, product: SupplierProduct) -> dict | None:
        """
        Returns the WooCommerce product dict if found, else None.
        """
        # 1) Look up by SKU
        wc_product = self._svc.find_by_sku(product.sku)
        if wc_product:
            logger.debug(f"Matched by SKU: {product.sku} → WC ID {wc_product['id']}")
            return wc_product

        # 2) Look up by supplier_url in meta_data
        wc_product = self._svc.find_by_meta(META_SUPPLIER_URL, product.supplier_url)
        if wc_product:
            logger.debug(
                f"Matched by supplier_url meta: {product.supplier_url} → WC ID {wc_product['id']}"
            )
            return wc_product

        return None

    def is_managed(self, wc_product: dict) -> bool:
        """Returns True if this WC product was created/managed by our sync."""
        meta = {m["key"]: m["value"] for m in wc_product.get("meta_data", [])}
        return str(meta.get(META_SYNC_MANAGED, "")).lower() in ("true", "1", "yes")
