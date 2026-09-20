"""
Unified product model.
Every supplier scraper must return a list[SupplierProduct].
This ensures all downstream logic (pricing, matching, WooCommerce) is supplier-agnostic.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SupplierProduct:
    # ── Identity ──────────────────────────────────────────────────
    supplier_name: str           # e.g. "Leopard"
    supplier_key: str            # e.g. "leopard"
    supplier_product_id: Optional[str]  # ID from supplier site, if available
    supplier_url: str            # canonical URL on supplier site
    sku: str                     # generated stable SKU

    # ── Content ───────────────────────────────────────────────────
    name: str
    original_description: str = ""

    # ── Pricing ───────────────────────────────────────────────────
    price: float = 0.0           # supplier's listed price
    calculated_price: float = 0.0  # after multiplier / rounding
    currency: str = "ILS"

    # ── Stock ─────────────────────────────────────────────────────
    stock_status: str = "instock"   # "instock" | "outofstock"
    is_available: bool = True

    # ── Categories ────────────────────────────────────────────────
    supplier_category: str = ""
    mapped_category: str = ""    # filled by CategoryMatcher

    # ── Images ────────────────────────────────────────────────────
    images: list[str] = field(default_factory=list)

    # ── Attributes ────────────────────────────────────────────────
    dimensions: str = ""
    width: str = ""
    depth: str = ""
    height: str = ""
    material: str = ""
    color: str = ""

    # ── Enrichment (filled after OpenAI) ──────────────────────────
    improved_name: str = ""
    short_description: str = ""
    full_description: str = ""
    seo_title: str = ""
    seo_meta_description: str = ""
    tags: list[str] = field(default_factory=list)
    product_highlights: list[str] = field(default_factory=list)
    ai_generated: bool = False

    # ── Review flags ──────────────────────────────────────────────
    requires_manual_review: bool = False
    manual_review_reason: str = ""
    status: str = "publish"      # "publish" | "draft"

    # ── Extra WooCommerce meta (key → value), merged into the product's
    #    meta_data on create/update. Used e.g. for per-product minimum order
    #    quantity (_min_order_qty). ──
    extra_meta: dict = field(default_factory=dict)

    def display_name(self) -> str:
        return self.improved_name or self.name

    def final_description(self) -> str:
        return self.full_description or self.original_description

    def mark_for_review(self, reason: str) -> None:
        self.requires_manual_review = True
        self.manual_review_reason = reason
        self.status = "draft"
