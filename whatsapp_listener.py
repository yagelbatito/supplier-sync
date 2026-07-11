"""
Entry point for the WhatsApp listener.

Run:  python whatsapp_listener.py

Constructs all services using the EXACT same pattern as main.py (so the
WhatsApp flow uses the same WC client, OpenAI enrichment, category matcher,
shipping rules, etc. as the regular scrapers) and starts the FastAPI
webhook server.
"""
import os
import sys

import uvicorn
from dotenv import load_dotenv

# Load env vars before any of our imports that read them.
load_dotenv()

# ── Existing-project imports (mirrors main.py) ──────────────────
from src.core.config_loader import (
    load_app_settings,
    load_category_mapping,
    load_enabled_suppliers,
    load_keyword_rules,
    load_shipping_class_mapping,
    load_suppliers,
)
from src.core.logger import get_logger
from src.enrichment.openai_client import OpenAIClient
from src.enrichment.product_content_generator import ProductContentGenerator
from src.matching.category_matcher import CategoryMatcher
from src.woocommerce.category_service import CategoryService
from src.woocommerce.client import WooCommerceClient
from src.woocommerce.media_service import MediaService
from src.woocommerce.product_service import ProductService

# ── New WhatsApp modules ────────────────────────────────────────
from src.whatsapp.meta_client import WhatsAppClient
from src.whatsapp.ocr_service import OcrService
from src.whatsapp.orchestrator import WhatsAppOrchestrator
from src.whatsapp.pending_store import PendingStore
from src.whatsapp.webhook_server import create_app

logger = get_logger("whatsapp_listener")


def build_orchestrator() -> WhatsAppOrchestrator:
    """Construct the orchestrator with all its dependencies wired up.

    Replicates the service-init block from main.main() so the WhatsApp flow
    uses identical configuration (WC URL, OpenAI key, category mapping,
    keyword rules, shipping classes) as the scrape-driven syncs.
    """
    # 1. App settings (WC URL/keys, OpenAI key, etc.)
    settings = load_app_settings()

    # 2. WooCommerce stack — same construction as main.py
    wc_client = WooCommerceClient(
        url=settings.woocommerce_url,
        consumer_key=settings.woocommerce_key,
        consumer_secret=settings.woocommerce_secret,
        wp_user=settings.wp_user,
        wp_app_password=settings.wp_app_password,
        verify_ssl=settings.verify_ssl,
        dry_run=getattr(settings, "dry_run", False),
    )
    product_svc = ProductService(wc_client)
    media_svc = MediaService(wc_client, verify_ssl=settings.verify_ssl)
    category_svc = CategoryService(wc_client)

    logger.info("Loading WooCommerce categories...")
    category_svc.load()

    # 3. Category matching + keyword rules + shipping classes
    category_mapping = load_category_mapping()
    keyword_rules = load_keyword_rules()
    shipping_class_map = load_shipping_class_mapping()
    if shipping_class_map:
        logger.info(f"Loaded {len(shipping_class_map)} category→shipping-class mappings")

    category_matcher = CategoryMatcher(
        category_mapping=category_mapping,
        keyword_rules=keyword_rules,
        wc_categories=category_svc.category_names,
    )

    # 4. OpenAI enrichment (uses settings.openai_api_key + settings.openai_model)
    ai_client = OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
    )
    content_generator = ProductContentGenerator(ai_client)

    # 5. Supplier configs.
    # We use load_suppliers() (not load_enabled_suppliers()) because we want
    # WhatsApp to be able to assign products to ANY supplier — even ones
    # disabled in the cron scrape. E.g. perlahome scraping might be off
    # while you still want to add perlahome items via WhatsApp.
    suppliers_config = load_suppliers()
    logger.info(f"Loaded {len(suppliers_config)} supplier configs: {', '.join(suppliers_config.keys())}")

    # 6. WhatsApp-specific stack
    wa_client = WhatsAppClient()
    ocr = OcrService()
    store = PendingStore()

    return WhatsAppOrchestrator(
        wa_client=wa_client,
        ocr=ocr,
        store=store,
        wc_client=wc_client,
        product_svc=product_svc,
        media_svc=media_svc,
        category_svc=category_svc,
        category_matcher=category_matcher,
        content_generator=content_generator,
        suppliers_config=suppliers_config,
        shipping_class_map=shipping_class_map,
    )


def main() -> int:
    # Required env vars — fail fast at startup.
    required = ["WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_VERIFY_TOKEN", "OPENAI_API_KEY"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        logger.error(f"Missing required env vars: {', '.join(missing)}")
        return 1

    try:
        orchestrator = build_orchestrator()
    except Exception as exc:
        logger.error(f"Failed to build orchestrator: {exc}", exc_info=True)
        return 1

    app = create_app(orchestrator)

    host = os.getenv("WHATSAPP_WEBHOOK_HOST", "0.0.0.0")
    # Cloud hosts (Render/Railway/Heroku) inject the port to bind via $PORT.
    port = int(os.getenv("PORT") or os.getenv("WHATSAPP_WEBHOOK_PORT", "8000"))
    logger.info(f"Starting WhatsApp webhook server on {host}:{port}")

    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
