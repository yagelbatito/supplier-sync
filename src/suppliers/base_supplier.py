"""
Abstract base class that every supplier scraper must extend.
Enforces the contract: scrape() → list[SupplierProduct]
"""
import os
from abc import ABC, abstractmethod

from src.core.config_loader import SupplierConfig
from src.core.logger import get_logger
from src.models.product import SupplierProduct
from src.pricing.price_calculator import calculate_price
from src.scraping.http_client import HttpClient


class BaseSupplier(ABC):
    def __init__(self, config: SupplierConfig, http: HttpClient):
        self.config = config
        self.http = http
        self.logger = get_logger(f"supplier.{config.key}")
        # When False, main.py's "disappeared products" check is skipped for
        # this supplier — drafting products we simply didn't see during a
        # partial scrape would hide live products from the storefront.
        self.scrape_was_complete: bool = True
        self.incomplete_reason: str = ""

    def mark_scrape_incomplete(self, reason: str) -> None:
        """Suppliers call this when a paged/listing endpoint fails midway
        so a downstream consumer can avoid drafting un-seen products."""
        if self.scrape_was_complete:
            self.scrape_was_complete = False
            self.incomplete_reason = reason
            self.logger.warning(
                f"Marking scrape incomplete — disappeared check will be skipped. Reason: {reason}"
            )

    @abstractmethod
    def scrape(self) -> list[SupplierProduct]:
        """
        Scrape all products from this supplier.
        Must return a list of SupplierProduct objects.
        Each product should have: name, price, supplier_url, images, stock_status.
        The base class will apply pricing after scraping.
        """
        ...

    @property
    def scrape_limit(self) -> int:
        """
        Soft limit on how many products to scrape, mirroring
        MAX_PRODUCTS_PER_SUPPLIER. Suppliers that respect this can early-exit
        their loop instead of scraping the whole catalog just to be sliced
        down later. 0 = no limit.

        NOTE: Only safe for testing. In production this skips the rest of the
        catalog, which means main.py's "disappeared products" check will draft
        anything the partial scrape didn't cover. Use with MAX_PRODUCTS_PER_SUPPLIER=0
        (the default) for real syncs.
        """
        try:
            return max(0, int(os.environ.get("MAX_PRODUCTS_PER_SUPPLIER", "0") or 0))
        except ValueError:
            return 0

    def should_stop_scraping(self, collected: list[SupplierProduct]) -> bool:
        """Helper for supplier loops — break when scrape_limit reached."""
        return self.scrape_limit > 0 and len(collected) >= self.scrape_limit

    def run(self) -> list[SupplierProduct]:
        """Public entry point. Calls scrape() and applies pricing."""
        self.logger.info(f"Starting scrape for supplier: {self.config.supplier_name}")
        try:
            products = self.scrape()
        except Exception as exc:
            self.logger.error(f"Fatal scrape error for {self.config.key}: {exc}", exc_info=True)
            raise

        self.logger.info(f"Scraped {len(products)} products from {self.config.supplier_name}")

        # Apply pricing
        for p in products:
            p.calculated_price = calculate_price(p.price, self.config)

        return products

    def _make_product(
        self,
        name: str,
        supplier_url: str,
        price: float,
        images: list[str],
        stock_status: str = "instock",
        supplier_product_id: str | None = None,
        supplier_category: str = "",
        original_description: str = "",
        **kwargs,
    ) -> SupplierProduct:
        """Convenience factory — subclasses use this to build products."""
        from src.core.utils import stable_sku

        sku = stable_sku(
            supplier_key=self.config.key,
            product_id=supplier_product_id,
            product_url=supplier_url,
            prefix=self.config.sku_prefix,
        )

        return SupplierProduct(
            supplier_name=self.config.supplier_name,
            supplier_key=self.config.key,
            supplier_product_id=supplier_product_id,
            supplier_url=supplier_url,
            sku=sku,
            name=name,
            original_description=original_description,
            price=price,
            stock_status=stock_status,
            is_available=stock_status == "instock",
            supplier_category=supplier_category,
            images=images,
            **kwargs,
        )
