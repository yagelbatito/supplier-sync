"""
Price calculation logic.
Each supplier has its own multiplier and rounding rules in suppliers.json.
"""
import math

from src.core.config_loader import SupplierConfig
from src.core.logger import get_logger

logger = get_logger(__name__)


def calculate_price(original_price: float, config: SupplierConfig) -> float:
    """
    Apply supplier price multiplier and optional rounding.

    Examples:
        Julian: 100 * 0.70 = 70.00
        Leopard: 100 * 0.90 = 90.00  → rounded to 89.90 if round_to=X.90
    """
    if original_price <= 0:
        return 0.0

    price = original_price * config.price_multiplier

    if config.round_prices:
        price = _round_price(price, config.round_to)

    logger.debug(
        f"[{config.key}] {original_price:.2f} × {config.price_multiplier} "
        f"= {price:.2f} (round_to={config.round_to})"
    )
    return price


def _round_price(price: float, round_to: str) -> float:
    """
    Round price according to rule:
      "X.00"  →  round to nearest whole number, e.g. 347.00
      "X.90"  →  round down to integer, subtract 0.10, e.g. 346.90
      "X.99"  →  round down to integer, subtract 0.01, e.g. 346.99
      "X.50"  →  round to nearest 0.50
    """
    rule = round_to.upper()

    if rule == "X.00":
        return round(price, 0)

    if rule == "X.90":
        return math.floor(price) - 0.10 if math.floor(price) >= price else math.floor(price) + 0.90

    if rule == "X.99":
        return math.floor(price) - 0.01 if math.floor(price) >= price else math.floor(price) + 0.99

    if rule == "X.50":
        return round(price * 2) / 2

    # Unknown rule → just round to 2dp
    return round(price, 2)


def format_price(price: float) -> str:
    """Format price as WooCommerce expects (string, no trailing zeros for .00)."""
    if price == int(price):
        return str(int(price))
    return f"{price:.2f}"
