"""
WooCommerce product CRUD operations.
All product-level interactions go through this service.
"""
import datetime as _dt
import os
from typing import Optional

from src.core.constants import (
    META_AI_GENERATED,
    META_CALCULATED_PRICE,
    META_LAST_PRICE_UPDATE,
    META_LAST_STOCK_UPDATE,
    META_LAST_SYNC_DATE,
    META_ORIGINAL_PRICE,
    META_SOURCE_IMAGE_URLS,
    META_STOCK_STATUS,
    META_SUPPLIER_NAME,
    META_SUPPLIER_PRODUCT_ID,
    META_SUPPLIER_URL,
    META_SYNC_MANAGED,
    META_YOAST_DESC,
    META_YOAST_TITLE,
    STATUS_DRAFT,
    STATUS_PUBLISH,
    STOCK_OUT,
)
from src.core.logger import get_logger
from src.core.utils import now_iso
from src.models.product import SupplierProduct
from src.pricing.price_calculator import format_price
from src.woocommerce.client import WooCommerceClient

logger = get_logger(__name__)

# Every NEW product created on/after this date is ALSO added to the store's
# "New collection" category (in addition to its own category) — so shoppers see
# genuinely new arrivals there. Set to a date AFTER the initial bulk migration
# so the migration itself doesn't flood the collection. Override with the
# NEW_COLLECTION_SINCE env var (YYYY-MM-DD); empty disables the feature.
NEW_COLLECTION_SINCE = os.getenv("NEW_COLLECTION_SINCE", "2026-09-27")
NEW_COLLECTION_NAME = "New collection"


class ProductService:
    def __init__(self, client: WooCommerceClient):
        self._client = client
        self._sku_cache: dict[str, dict] = {}   # SKU → WC product dict
        self._meta_cache: dict[str, dict] = {}  # meta value → WC product dict
        self._new_col_id: Optional[int] = None  # lazy "New collection" category id

    def _new_collection_id(self) -> Optional[int]:
        """Resolve (and cache) the 'New collection' category id, or None."""
        if self._new_col_id is not None:
            return self._new_col_id or None
        try:
            res = self._client.get("products/categories",
                                   params={"search": NEW_COLLECTION_NAME, "per_page": 20})
            for c in res:
                if c.get("name") == NEW_COLLECTION_NAME:
                    self._new_col_id = c["id"]
                    return self._new_col_id
        except Exception as exc:
            logger.warning(f"New-collection lookup failed: {exc}")
        self._new_col_id = 0
        return None

    def _with_new_collection(self, category_ids: list) -> list:
        """Append the 'New collection' category to NEW products, but only from
        NEW_COLLECTION_SINCE onward (keeps the bulk migration out of it)."""
        if not NEW_COLLECTION_SINCE:
            return category_ids
        if _dt.date.today().isoformat() < NEW_COLLECTION_SINCE:
            return category_ids
        ncid = self._new_collection_id()
        if ncid and ncid not in category_ids:
            return list(category_ids) + [ncid]
        return category_ids

    def preload_managed_products(self, supplier_key: str) -> list[dict]:
        """
        Load all WC products managed by this supplier.
        Used at the end of each supplier run to find products that disappeared.
        """
        logger.info(f"Preloading managed products for supplier: {supplier_key}")
        all_products = self._client.get_all(
            "products",
            status="any",
        )
        # Filter to managed ones belonging to this supplier
        managed = []
        for p in all_products:
            meta = {m["key"]: m["value"] for m in p.get("meta_data", [])}
            if (
                str(meta.get(META_SYNC_MANAGED, "")).lower() in ("true", "1")
                and meta.get(META_SUPPLIER_NAME, "").lower() == supplier_key.lower()
            ):
                managed.append(p)
                # Pre-populate the SKU cache so later find_by_sku() doesn't
                # hit the API and (critically) finds drafts that the bare
                # /products?sku=X endpoint sometimes misses.
                sku = p.get("sku")
                if sku:
                    self._sku_cache[sku] = p
        logger.info(f"Found {len(managed)} managed products for {supplier_key}")
        return managed

    def find_by_sku(self, sku: str) -> Optional[dict]:
        if sku in self._sku_cache:
            return self._sku_cache[sku]
        # status=any covers everything EXCEPT trash. Trashed products still
        # reserve their SKU at the DB level, so we check trash separately —
        # otherwise WC rejects the create with "SKU already exists".
        for status in ("any", "trash"):
            try:
                results = self._client.get(
                    "products",
                    params={"sku": sku, "status": status, "per_page": 1},
                )
                if results:
                    self._sku_cache[sku] = results[0]
                    return results[0]
            except Exception as exc:
                logger.warning(f"SKU lookup (status={status}) failed for {sku}: {exc}")
        return None

    def find_by_meta(self, meta_key: str, meta_value: str) -> Optional[dict]:
        cache_key = f"{meta_key}:{meta_value}"
        if cache_key in self._meta_cache:
            return self._meta_cache[cache_key]
        # WooCommerce doesn't support direct meta search in REST API,
        # so we search by sku first, then iterate if needed.
        # This is a known limitation; the SKU is the primary key.
        return None

    def create(self, product: SupplierProduct, category_ids: list[int], images_payload: list[dict], shipping_class: str = "") -> dict:
        category_ids = self._with_new_collection(category_ids)   # new arrivals → also "New collection"
        payload = self._build_payload(product, category_ids, images_payload, shipping_class=shipping_class, is_new=True)
        result = self._client.post("products", payload)
        logger.info(f"Created WC product ID={result.get('id')} SKU={product.sku}")
        return result

    def update(self, wc_id: int, product: SupplierProduct, category_ids: list[int], images_payload: list[dict], shipping_class: str = "") -> dict:
        payload = self._build_update_payload(product, category_ids, images_payload, shipping_class=shipping_class)
        result = self._client.put(f"products/{wc_id}", payload)
        logger.info(f"Updated WC product ID={wc_id} SKU={product.sku}")
        return result

    def update_price_only(self, wc_id: int, product: SupplierProduct) -> dict:
        payload = {
            "regular_price": format_price(product.calculated_price),
            "meta_data": [
                {"key": META_ORIGINAL_PRICE, "value": str(product.price)},
                {"key": META_CALCULATED_PRICE, "value": format_price(product.calculated_price)},
                {"key": META_LAST_PRICE_UPDATE, "value": now_iso()},
                {"key": META_LAST_SYNC_DATE, "value": now_iso()},
            ],
        }
        return self._client.put(f"products/{wc_id}", payload)

    def update_stock_only(self, wc_id: int, stock_status: str) -> dict:
        payload = {
            "stock_status": stock_status,
            "meta_data": [
                {"key": META_STOCK_STATUS, "value": stock_status},
                {"key": META_LAST_STOCK_UPDATE, "value": now_iso()},
                {"key": META_LAST_SYNC_DATE, "value": now_iso()},
            ],
        }
        return self._client.put(f"products/{wc_id}", payload)

    def set_draft(self, wc_id: int) -> dict:
        return self._client.put(f"products/{wc_id}", {"status": STATUS_DRAFT})

    def set_publish(self, wc_id: int) -> dict:
        return self._client.put(f"products/{wc_id}", {"status": STATUS_PUBLISH})

    def set_out_of_stock(self, wc_id: int) -> dict:
        return self._client.put(f"products/{wc_id}", {
            "stock_status": STOCK_OUT,
            "status": STATUS_DRAFT,
        })

    # ── Payload builders ─────────────────────────────────────────

    def _build_payload(
        self,
        product: SupplierProduct,
        category_ids: list[int],
        images_payload: list[dict],
        shipping_class: str = "",
        is_new: bool = False,
    ) -> dict:
        from slugify import slugify

        name = product.display_name()
        payload: dict = {
            "name": name,
            "type": "simple",
            "sku": product.sku,
            "regular_price": format_price(product.calculated_price),
            "description": product.final_description(),
            "short_description": product.short_description,
            "status": product.status,
            "stock_status": product.stock_status,
            "categories": [{"id": cid} for cid in category_ids],
            "tags": [{"name": t} for t in product.tags],
            "images": images_payload,
            "meta_data": self._build_meta(product),
        }

        # Native WooCommerce dimensions (cm). depth → length (WC has no
        # "depth"). Only sent when we actually parsed a value, so an update
        # never wipes existing dimensions with blanks.
        dims = {}
        if getattr(product, "depth", ""):
            dims["length"] = str(product.depth)
        if getattr(product, "width", ""):
            dims["width"] = str(product.width)
        if getattr(product, "height", ""):
            dims["height"] = str(product.height)
        if dims:
            payload["dimensions"] = dims

        # Only set shipping_class if we have one — empty string would clear
        # an existing class on the product during an update.
        if shipping_class:
            payload["shipping_class"] = shipping_class

        if is_new:
            payload["slug"] = slugify(name)

        return payload

    def _build_update_payload(
        self,
        product: SupplierProduct,
        category_ids: list[int],
        images_payload: list[dict],
        shipping_class: str = "",
    ) -> dict:
        payload = self._build_payload(product, category_ids, images_payload, shipping_class=shipping_class, is_new=False)
        # Don't overwrite name/description on update unless content was regenerated
        if not product.ai_generated:
            payload.pop("name", None)
            payload.pop("description", None)
            payload.pop("short_description", None)
        # Never clear existing images with a blank list — an update that passes
        # no images should leave the product's images untouched (and skips the
        # slow WC image side-load).
        if not images_payload:
            payload.pop("images", None)
        return payload

    def _build_meta(self, product: SupplierProduct) -> list[dict]:
        meta = [
            {"key": META_SYNC_MANAGED, "value": "true"},
            {"key": META_SUPPLIER_NAME, "value": product.supplier_name},
            {"key": META_SUPPLIER_PRODUCT_ID, "value": product.supplier_product_id or ""},
            {"key": META_SUPPLIER_URL, "value": product.supplier_url},
            {"key": META_ORIGINAL_PRICE, "value": str(product.price)},
            {"key": META_CALCULATED_PRICE, "value": format_price(product.calculated_price)},
            {"key": META_STOCK_STATUS, "value": product.stock_status},
            {"key": META_SOURCE_IMAGE_URLS, "value": ",".join(product.images[:5])},
            {"key": META_AI_GENERATED, "value": "true" if product.ai_generated else "false"},
            {"key": META_LAST_SYNC_DATE, "value": now_iso()},
        ]

        if product.seo_title:
            meta.append({"key": META_YOAST_TITLE, "value": product.seo_title})
        if product.seo_meta_description:
            meta.append({"key": META_YOAST_DESC, "value": product.seo_meta_description})

        # Extra per-product meta (e.g. _min_order_qty) — set by the supplier sync.
        for k, v in (getattr(product, "extra_meta", None) or {}).items():
            meta.append({"key": k, "value": str(v)})

        return meta
