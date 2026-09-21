"""
Loads all configuration: env vars, suppliers.json, category mappings, keyword rules.
Single source of truth for all settings.
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from src.core.exceptions import ConfigurationError
from src.core.logger import get_logger

logger = get_logger(__name__)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


# ──────────────────────────────────────────────
# Env
# ──────────────────────────────────────────────

def load_env() -> None:
    """Load .env file if present."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(env_path)
    logger.debug(f"Loaded .env from {env_path}")


def require_env(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise ConfigurationError(f"Missing required environment variable: {key}")
    return val


def optional_env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


# ──────────────────────────────────────────────
# JSON config helpers
# ──────────────────────────────────────────────

def _load_json(filename: str) -> dict:
    path = CONFIG_DIR / filename
    if not path.exists():
        raise ConfigurationError(f"Config file not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────
# Supplier config dataclass
# ──────────────────────────────────────────────

@dataclass
class SupplierConfig:
    key: str                          # e.g. "julian"
    supplier_name: str
    base_url: str
    enabled: bool
    price_multiplier: float
    round_prices: bool
    round_to: str                     # "X.00" or "X.90"
    draft_when_out_of_stock: bool
    allow_republish: bool
    sync_images: bool
    regenerate_content: bool
    default_category: str
    scraper_type: str                 # "requests" | "selenium"
    sku_prefix: str
    notes: str = ""
    extra: dict = field(default_factory=dict)  # any future keys


def load_suppliers() -> dict[str, SupplierConfig]:
    """Return all supplier configs keyed by supplier key."""
    raw = _load_json("suppliers.json")
    suppliers: dict[str, SupplierConfig] = {}
    for key, cfg in raw.items():
        known_keys = {
            "supplier_name", "base_url", "enabled", "price_multiplier",
            "round_prices", "round_to", "draft_when_out_of_stock",
            "allow_republish", "sync_images", "regenerate_content",
            "default_category", "scraper_type", "sku_prefix", "notes",
        }
        extra = {k: v for k, v in cfg.items() if k not in known_keys}
        suppliers[key] = SupplierConfig(
            key=key,
            supplier_name=cfg.get("supplier_name", key),
            base_url=cfg.get("base_url", ""),
            enabled=cfg.get("enabled", False),
            price_multiplier=float(cfg.get("price_multiplier", 1.0)),
            round_prices=cfg.get("round_prices", False),
            round_to=cfg.get("round_to", "X.00"),
            draft_when_out_of_stock=cfg.get("draft_when_out_of_stock", True),
            allow_republish=cfg.get("allow_republish", True),
            sync_images=cfg.get("sync_images", True),
            regenerate_content=cfg.get("regenerate_content", False),
            default_category=cfg.get("default_category", "מוצרים נוספים"),
            scraper_type=cfg.get("scraper_type", "requests"),
            sku_prefix=cfg.get("sku_prefix", key[:3].upper()),
            notes=cfg.get("notes", ""),
            extra=extra,
        )
    logger.info(f"Loaded {len(suppliers)} supplier configs")
    return suppliers


def load_enabled_suppliers() -> dict[str, SupplierConfig]:
    return {k: v for k, v in load_suppliers().items() if v.enabled}


def load_category_mapping() -> dict[str, dict[str, str]]:
    """Returns {supplier_key: {supplier_category: our_category}}"""
    return _load_json("category_mapping.json")


def load_keyword_rules() -> dict[str, list[str]]:
    """Returns {our_category: [keyword, ...]}"""
    return _load_json("keyword_category_rules.json")


def load_shipping_class_mapping() -> dict[str, str]:
    """
    Returns {mapped_category: shipping_class_slug}.
    The slug must match a Shipping Class slug configured in WooCommerce.
    File is optional — returns {} if missing.
    """
    path = CONFIG_DIR / "shipping_class_mapping.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    # Drop any keys starting with "_" (comments)
    return {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, str) and v}


# ──────────────────────────────────────────────
# App settings from env
# ──────────────────────────────────────────────

@dataclass
class AppSettings:
    woocommerce_url: str
    woocommerce_key: str
    woocommerce_secret: str
    wp_user: str
    wp_app_password: str
    openai_api_key: str
    openai_model: str
    dry_run: bool
    verify_ssl: bool
    request_delay: float
    max_products_per_supplier: int
    log_level: str


def load_app_settings() -> AppSettings:
    load_env()
    return AppSettings(
        woocommerce_url=require_env("WOOCOMMERCE_URL").rstrip("/"),
        woocommerce_key=require_env("WOOCOMMERCE_KEY"),
        woocommerce_secret=require_env("WOOCOMMERCE_SECRET"),
        wp_user=optional_env("WP_USER"),
        wp_app_password=optional_env("WP_APP_PASSWORD"),
        openai_api_key=optional_env("OPENAI_API_KEY"),
        openai_model=optional_env("OPENAI_MODEL", "gpt-4o-mini"),
        dry_run=optional_env("DRY_RUN", "false").lower() in ("1", "true", "yes"),
        verify_ssl=optional_env("VERIFY_SSL", "true").lower() in ("1", "true", "yes"),
        request_delay=float(optional_env("REQUEST_DELAY", "1.0")),
        max_products_per_supplier=int(optional_env("MAX_PRODUCTS_PER_SUPPLIER", "0")),
        log_level=optional_env("LOG_LEVEL", "INFO"),
    )
