"""Application-wide constants."""

# Meta keys stored on each managed WooCommerce product
META_SUPPLIER_NAME = "supplier_name"
META_SUPPLIER_PRODUCT_ID = "supplier_product_id"
META_SUPPLIER_URL = "supplier_url"
META_SYNC_MANAGED = "sync_managed"
META_LAST_SYNC_DATE = "last_supplier_sync_date"
META_ORIGINAL_PRICE = "original_supplier_price"
META_CALCULATED_PRICE = "calculated_store_price"
META_STOCK_STATUS = "supplier_stock_status"
META_SOURCE_IMAGE_URLS = "source_image_urls"
META_AI_GENERATED = "content_generated_by_ai"
META_LAST_PRICE_UPDATE = "last_price_update_date"
META_LAST_STOCK_UPDATE = "last_stock_update_date"

# SEO plugin meta keys (Yoast)
META_YOAST_TITLE = "_yoast_wpseo_title"
META_YOAST_DESC = "_yoast_wpseo_metadesc"

# WooCommerce product statuses
STATUS_PUBLISH = "publish"
STATUS_DRAFT = "draft"

# WooCommerce stock statuses
STOCK_IN = "instock"
STOCK_OUT = "outofstock"

# Default fallback category name
DEFAULT_REVIEW_CATEGORY = "מוצרים נוספים"

# CSV source identifier
CSV_SOURCE_NAME = "csv"
GOOGLE_SHEETS_SOURCE_NAME = "google_sheets"

# HTTP settings
DEFAULT_TIMEOUT = 30
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; SupplierSyncBot/1.0)"
