"""Custom exceptions for supplier-sync."""


class SupplierSyncError(Exception):
    """Base exception for all sync errors."""


class SupplierScrapingError(SupplierSyncError):
    """Raised when scraping a supplier fails."""


class ProductParsingError(SupplierSyncError):
    """Raised when a product page cannot be parsed."""


class WooCommerceError(SupplierSyncError):
    """Raised when a WooCommerce API call fails."""


class MediaUploadError(SupplierSyncError):
    """Raised when a WordPress media upload fails."""


class CategoryMappingError(SupplierSyncError):
    """Raised when a category cannot be mapped."""


class EnrichmentError(SupplierSyncError):
    """Raised when OpenAI enrichment fails."""


class ConfigurationError(SupplierSyncError):
    """Raised when configuration is invalid or missing."""
