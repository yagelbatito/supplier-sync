"""
supplier-sync — main entry point.
Orchestrates the full sync pipeline for all enabled suppliers.

Usage:
    python -m src.main                           # sync all enabled suppliers
    python -m src.main --supplier leopard        # sync one supplier
    python -m src.main --dry-run                 # dry run (no WC writes)
    python -m src.main --supplier csv --csv-file /path/to/products.csv
    python -m src.main --supplier csv --sheets-url "https://..."
"""
import argparse
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

from src.core.config_loader import (
    AppSettings,
    SupplierConfig,
    load_app_settings,
    load_category_mapping,
    load_enabled_suppliers,
    load_keyword_rules,
    load_shipping_class_mapping,
)
from src.core.constants import STATUS_DRAFT, STOCK_IN, STOCK_OUT
from src.core.exceptions import SupplierSyncError
from src.core.logger import get_logger
from src.enrichment.openai_client import OpenAIClient
from src.enrichment.product_content_generator import ProductContentGenerator
from src.matching.category_matcher import CategoryMatcher
from src.matching.product_matcher import ProductMatcher
from src.models.product import SupplierProduct
from src.models.sync_result import ProductResult, RunReport, SupplierSyncResult
from src.reports.sync_report import print_run_summary, print_supplier_summary, save_report
from src.scraping.http_client import HttpClient
from src.woocommerce.category_service import CategoryService
from src.woocommerce.client import WooCommerceClient
from src.woocommerce.media_service import MediaService
from src.woocommerce.product_service import ProductService

logger = get_logger("supplier_sync")


# ──────────────────────────────────────────────────────────────────
# Supplier factory
# ──────────────────────────────────────────────────────────────────

def build_supplier(
    key: str,
    config: SupplierConfig,
    http: HttpClient,
    csv_path: Optional[str] = None,
    sheets_url: Optional[str] = None,
):
    """Instantiate the correct supplier class by key."""
    if key == "leopard":
        from src.suppliers.leopard_supplier import LeopardSupplier
        return LeopardSupplier(config, http)

    if key == "floralis":
        from src.suppliers.floralis_supplier import FloralisSupplier
        return FloralisSupplier(config, http)

    if key == "julian":
        from src.suppliers.julian_supplier import JulianSupplier
        return JulianSupplier(config, http)


    if key == "home21":
        from src.suppliers.home21_supplier import Home21Supplier
        return Home21Supplier(config, http)
    if key == "paldinox":
        from src.suppliers.paldinox_supplier import PaldinoxSupplier
        return PaldinoxSupplier(config, http)

    if key == "perlahome":
        from src.suppliers.perlahome_supplier import PerlahomeSupplier
        return PerlahomeSupplier(config, http)

    if key == "csv":
        from src.suppliers.csv_supplier import CsvSupplier
        return CsvSupplier(config, http, csv_path=csv_path, sheets_url=sheets_url)

    raise SupplierSyncError(f"Unknown supplier key: '{key}'")


# ──────────────────────────────────────────────────────────────────
# Single supplier sync
# ──────────────────────────────────────────────────────────────────

def sync_supplier(
    key: str,
    config: SupplierConfig,
    settings: AppSettings,
    wc_client: WooCommerceClient,
    product_svc: ProductService,
    media_svc: MediaService,
    category_svc: CategoryService,
    category_matcher: CategoryMatcher,
    content_generator: ProductContentGenerator,
    shipping_class_map: dict[str, str],
    csv_path: Optional[str] = None,
    sheets_url: Optional[str] = None,
) -> SupplierSyncResult:

    result = SupplierSyncResult(supplier_key=key, supplier_name=config.supplier_name)
    logger.info(f"\n{'─'*55}")
    logger.info(f"Starting supplier: {config.supplier_name} [{key}]")

    # ── 1. Preload managed products (for deactivation at end) ─────
    try:
        managed_products_before = product_svc.preload_managed_products(key)
    except Exception as exc:
        logger.warning(f"Could not preload managed products: {exc}")
        managed_products_before = []

    # ── 2. Scrape ──────────────────────────────────────────────────
    http = HttpClient(
        verify_ssl=settings.verify_ssl,
        request_delay=settings.request_delay,
    )

    try:
        supplier = build_supplier(key, config, http, csv_path=csv_path, sheets_url=sheets_url)
        products = supplier.run()
    except Exception as exc:
        logger.error(f"[{key}] Fatal scrape error: {exc}", exc_info=True)
        result.errors.append(str(exc))
        result.finish()
        return result

    result.total_scraped = len(products)

    # Skip products with no/zero price — supplier shows "contact for price"
    # or a placeholder we shouldn't republish.
    invalid_priced = [p for p in products if not p.price or p.price <= 0]
    if invalid_priced:
        logger.info(f"[{key}] Skipping {len(invalid_priced)} products with no/zero price")
        for p in invalid_priced[:5]:
            logger.debug(f"  → skipped (price={p.price}): {p.name[:60]} [{p.sku}]")
    products = [p for p in products if p.price and p.price > 0]

    # Limit products if configured
    if settings.max_products_per_supplier > 0:
        products = products[: settings.max_products_per_supplier]
        logger.info(f"[{key}] Limiting to {len(products)} products (MAX_PRODUCTS_PER_SUPPLIER)")

    # ── 3. Process each product ────────────────────────────────────
    seen_skus: set[str] = set()

    for product in products:
        try:
            _process_product(
                product=product,
                config=config,
                product_svc=product_svc,
                media_svc=media_svc,
                category_svc=category_svc,
                category_matcher=category_matcher,
                content_generator=content_generator,
                shipping_class_map=shipping_class_map,
                result=result,
                seen_skus=seen_skus,
            )
        except Exception as exc:
            logger.error(f"[{key}] Unhandled error for {product.supplier_url}: {exc}", exc_info=True)
            result.add_product(ProductResult(
                sku=product.sku,
                name=product.name,
                supplier_url=product.supplier_url,
                action="failed",
                error=str(exc),
            ))

    # ── 4. Deactivate products that disappeared from supplier ──────
    # Skip if the scrape was partial — a missing product just means we
    # didn't see it this run, not that it was removed from the supplier.
    if not supplier.scrape_was_complete:
        logger.warning(
            f"[{key}] Skipping disappeared check — scrape was incomplete "
            f"({supplier.incomplete_reason}). "
            f"Re-run when the supplier endpoint is healthy."
        )
    else:
        logger.info(f"[{key}] Checking for disappeared products...")
        for wc_product in managed_products_before:
            wc_sku = wc_product.get("sku", "")
            wc_id = wc_product.get("id")
            wc_name = wc_product.get("name", "")

            if wc_sku and wc_sku not in seen_skus and wc_id:
                logger.info(f"[{key}] Product disappeared → draft: {wc_name} (ID={wc_id})")
                try:
                    product_svc.set_draft(wc_id)
                    result.drafted += 1
                    result.add_product(ProductResult(
                        sku=wc_sku,
                        name=wc_name,
                        supplier_url="",
                        action="drafted",
                    ))
                except Exception as exc:
                    logger.warning(f"[{key}] Could not draft {wc_id}: {exc}")

    result.finish()
    print_supplier_summary(result)
    return result


def _process_product(
    product: SupplierProduct,
    config: SupplierConfig,
    product_svc: ProductService,
    media_svc: MediaService,
    category_svc: CategoryService,
    category_matcher: CategoryMatcher,
    content_generator: ProductContentGenerator,
    shipping_class_map: dict[str, str],
    result: SupplierSyncResult,
    seen_skus: set[str],
) -> None:

    seen_skus.add(product.sku)
    logger.debug(f"Processing: {product.name[:60]} [{product.sku}]")

    # ── Category matching ──────────────────────────────────────────
    product.mapped_category = category_matcher.match(
        supplier_key=config.key,
        supplier_category=product.supplier_category,
        product_name=product.name,
        product_description=product.original_description,
        default_category=config.default_category,
    )

    # ── Handle out-of-stock ───────────────────────────────────────
    if not product.is_available and config.draft_when_out_of_stock:
        product.status = STATUS_DRAFT
        product.stock_status = STOCK_OUT

    # ── Check if exists in WooCommerce ────────────────────────────
    product_matcher = ProductMatcher(product_svc)
    existing = product_matcher.find_existing(product)

    category_ids = category_svc.resolve_list(product.mapped_category, config.default_category)
    shipping_class = shipping_class_map.get(product.mapped_category, "")

    if existing:
        _update_existing(product, existing, config, product_svc, media_svc, category_ids, content_generator, shipping_class, result)
    else:
        _create_new(product, config, product_svc, media_svc, category_ids, content_generator, shipping_class, result)


MAX_IMAGES_PER_PRODUCT = 5
_SKIP_URL_FRAGMENTS = ("logo", "icon", "banner", "placeholder", "sprite", ".svg", "pixel", "1x1")


def _build_images_payload(product: SupplierProduct, config: SupplierConfig) -> list[dict]:
    """
    Build the WooCommerce 'images' payload from raw URLs.
    WC will fetch and store each image itself — no WP Media API auth required.
    """
    if not config.sync_images or not product.images:
        return []

    payload: list[dict] = []
    seen: set[str] = set()
    for url in product.images:
        if not url or not url.startswith(("http://", "https://")):
            continue
        if any(s in url.lower() for s in _SKIP_URL_FRAGMENTS):
            continue
        if url in seen:
            continue
        seen.add(url)
        payload.append({"src": url, "position": len(payload)})
        if len(payload) >= MAX_IMAGES_PER_PRODUCT:
            break
    return payload


def _create_new(
    product: SupplierProduct,
    config: SupplierConfig,
    product_svc: ProductService,
    media_svc: MediaService,
    category_ids: list[int],
    content_generator: ProductContentGenerator,
    shipping_class: str,
    result: SupplierSyncResult,
) -> None:
    # Enrich content via AI
    if content_generator.available:
        product = content_generator.enrich(product)

        # For suppliers that require auth to download images (e.g. Paldinox B2B),
        # download via media_svc first and send attachment IDs instead of URLs.
    if config.key == "paldinox" and config.sync_images and product.images:
        image_ids = media_svc.upload_product_images(product.images)
        logger.info(f"  🖼️ Paldinox image_ids: {image_ids}")  # ← הוסיפי את זה
        images_payload = [{"id": mid} for mid in image_ids] if image_ids else []
    else:
        images_payload = _build_images_payload(product, config)
    if not images_payload and not product.images:
        product.mark_for_review("No images available")

    # Create in WooCommerce
    wc_product = product_svc.create(product, category_ids, images_payload, shipping_class=shipping_class)
    logger.info(f"  ✅ Created: {product.display_name()} → WC ID {wc_product.get('id')}")

    result.add_product(ProductResult(
        sku=product.sku,
        name=product.display_name(),
        supplier_url=product.supplier_url,
        action="created",
        requires_review=product.requires_manual_review,
        review_reason=product.manual_review_reason,
    ))


def _update_existing(
    product: SupplierProduct,
    existing: dict,
    config: SupplierConfig,
    product_svc: ProductService,
    media_svc: MediaService,
    category_ids: list[int],
    content_generator: ProductContentGenerator,
    shipping_class: str,
    result: SupplierSyncResult,
) -> None:
    wc_id = existing["id"]
    action = "updated"
    was_trashed = existing.get("status") == "trash"

    # If the product was in trash (manual cleanup or out-of-stock purge),
    # do a full restore: re-enrich, push all fields including status, so
    # the product comes back live with the latest fixes (correct category,
    # working images, etc.).
    if was_trashed:
        logger.info(f"  ♻️  Restoring trashed product (ID={wc_id}) [{product.sku}]")
        if content_generator.available:
            product = content_generator.enrich(product)
        images_payload = _build_images_payload(product, config)
        product_svc.update(wc_id, product, category_ids, images_payload, shipping_class=shipping_class)
        action = "restored"
    else:
        # Handle stock status change
        wc_stock = existing.get("stock_status", STOCK_IN)
        if product.stock_status != wc_stock:
            if not product.is_available and config.draft_when_out_of_stock:
                product_svc.set_out_of_stock(wc_id)
                result.out_of_stock += 1
                action = "drafted"
            elif product.is_available and config.allow_republish:
                product_svc.set_publish(wc_id)

        # Handle price change
        wc_price_str = existing.get("regular_price", "0") or "0"
        try:
            wc_price = float(wc_price_str)
        except ValueError:
            wc_price = 0.0

        price_changed = abs(product.calculated_price - wc_price) > 0.5
        if price_changed:
            product_svc.update_price_only(wc_id, product)
            logger.info(f"  💰 Price updated: {wc_price} → {product.calculated_price} [{product.sku}]")

        # Regenerate content if configured
        if config.regenerate_content and content_generator.available:
            product = content_generator.enrich(product)
            images_payload = _build_images_payload(product, config)
            product_svc.update(wc_id, product, category_ids, images_payload, shipping_class=shipping_class)
        else:
            # Just update stock + price meta
            product_svc.update_stock_only(wc_id, product.stock_status)

    logger.info(f"  🔄 Updated: {product.name[:50]} (ID={wc_id})")

    result.add_product(ProductResult(
        sku=product.sku,
        name=product.name,
        supplier_url=product.supplier_url,
        action=action,
        requires_review=product.requires_manual_review,
        review_reason=product.manual_review_reason,
    ))


# ──────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Supplier → WooCommerce sync")
    parser.add_argument("--supplier", help="Run specific supplier key (e.g. leopard, csv)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without writing to WooCommerce")
    parser.add_argument("--csv-file", help="Path to CSV file (when --supplier csv)")
    parser.add_argument("--sheets-url", help="Google Sheets export URL (when --supplier csv)")
    args = parser.parse_args()

    # ── Load settings ─────────────────────────────────────────────
    try:
        settings = load_app_settings()
    except Exception as exc:
        print(f"❌ Configuration error: {exc}")
        return 1

    if args.dry_run:
        settings.dry_run = True
        logger.info("🧪 DRY-RUN mode — no changes will be made to WooCommerce")

    # ── Load supplier configs ─────────────────────────────────────
    all_suppliers = load_enabled_suppliers()

    if args.supplier:
        if args.supplier not in all_suppliers and args.supplier != "csv":
            # Load all suppliers (including disabled) for single-run
            from src.core.config_loader import load_suppliers
            all_sups = load_suppliers()
            if args.supplier in all_sups:
                all_suppliers = {args.supplier: all_sups[args.supplier]}
            else:
                logger.error(f"Unknown supplier: '{args.supplier}'")
                return 1
        else:
            all_suppliers = {k: v for k, v in all_suppliers.items() if k == args.supplier}

    if not all_suppliers:
        logger.error("No enabled suppliers found. Check config/suppliers.json")
        return 1

    # ── Init shared services ──────────────────────────────────────
    wc_client = WooCommerceClient(
        url=settings.woocommerce_url,
        consumer_key=settings.woocommerce_key,
        consumer_secret=settings.woocommerce_secret,
        wp_user=settings.wp_user,
        wp_app_password=settings.wp_app_password,
        verify_ssl=settings.verify_ssl,
        dry_run=settings.dry_run,
    )

    product_svc = ProductService(wc_client)
    media_svc = MediaService(wc_client, verify_ssl=settings.verify_ssl)
    category_svc = CategoryService(wc_client)

    logger.info("Loading WooCommerce categories...")
    category_svc.load()

    category_mapping = load_category_mapping()
    keyword_rules = load_keyword_rules()
    shipping_class_map = load_shipping_class_mapping()
    if shipping_class_map:
        logger.info(f"Loaded {len(shipping_class_map)} category→shipping-class mappings")
    else:
        logger.info("No shipping_class_mapping.json found — products will be created without shipping_class")

    category_matcher = CategoryMatcher(
        category_mapping=category_mapping,
        keyword_rules=keyword_rules,
        wc_categories=category_svc.category_names,
    )

    ai_client = OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
    )
    content_generator = ProductContentGenerator(ai_client)

    # ── Run ───────────────────────────────────────────────────────
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    report = RunReport(run_id=run_id)

    logger.info(f"Starting sync run: {run_id}")
    logger.info(f"Suppliers: {', '.join(all_suppliers.keys())}")

    for key, config in all_suppliers.items():
        try:
            supplier_result = sync_supplier(
                key=key,
                config=config,
                settings=settings,
                wc_client=wc_client,
                product_svc=product_svc,
                media_svc=media_svc,
                category_svc=category_svc,
                category_matcher=category_matcher,
                content_generator=content_generator,
                shipping_class_map=shipping_class_map,
                csv_path=args.csv_file,
                sheets_url=args.sheets_url,
            )
            report.supplier_results.append(supplier_result)
        except Exception as exc:
            # One supplier failure must NOT stop other suppliers
            logger.error(f"❌ Supplier '{key}' failed entirely: {exc}", exc_info=True)
            failed_result = SupplierSyncResult(supplier_key=key, supplier_name=config.supplier_name)
            failed_result.errors.append(str(exc))
            failed_result.finish()
            report.supplier_results.append(failed_result)

    report.finish()
    print_run_summary(report)
    save_report(report)

    total_failures = sum(r.failed for r in report.supplier_results)
    return 0 if total_failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
