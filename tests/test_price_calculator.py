"""Unit tests for price_calculator."""

import pytest

from src.core.config_loader import SupplierConfig
from src.pricing.price_calculator import calculate_price, format_price


def make_config(**kwargs) -> SupplierConfig:
    defaults = dict(
        key="test", supplier_name="Test", base_url="", enabled=True,
        price_multiplier=1.0, round_prices=False, round_to="X.00",
        draft_when_out_of_stock=True, allow_republish=True, sync_images=True,
        regenerate_content=False, default_category="בדיקה ידנית",
        scraper_type="requests", sku_prefix="TST",
    )
    defaults.update(kwargs)
    return SupplierConfig(**defaults)


class TestCalculatePrice:
    def test_basic_multiplier(self):
        config = make_config(price_multiplier=0.70, round_prices=False)
        assert calculate_price(100.0, config) == pytest.approx(70.0)

    def test_floralis_multiplier(self):
        config = make_config(price_multiplier=0.80, round_prices=False)
        assert calculate_price(100.0, config) == pytest.approx(80.0)

    def test_zero_price_returns_zero(self):
        config = make_config(price_multiplier=0.70)
        assert calculate_price(0.0, config) == 0.0

    def test_negative_price_returns_zero(self):
        config = make_config(price_multiplier=0.70)
        assert calculate_price(-50.0, config) == 0.0

    def test_rounding_x00(self):
        config = make_config(price_multiplier=1.0, round_prices=True, round_to="X.00")
        result = calculate_price(347.67, config)
        assert result == 348.0

    def test_rounding_x90(self):
        config = make_config(price_multiplier=1.0, round_prices=True, round_to="X.90")
        result = calculate_price(350.0, config)
        assert result == pytest.approx(349.90)

    def test_full_pipeline_julian(self):
        """Julian: 100 * 0.70 = 70.00"""
        config = make_config(price_multiplier=0.70, round_prices=True, round_to="X.00")
        assert calculate_price(100.0, config) == 70.0

    def test_large_price(self):
        config = make_config(price_multiplier=0.85, round_prices=True, round_to="X.00")
        result = calculate_price(5000.0, config)
        assert result == 4250.0


class TestFormatPrice:
    def test_integer_price(self):
        assert format_price(70.0) == "70"

    def test_decimal_price(self):
        assert format_price(69.90) == "69.90"

    def test_zero(self):
        assert format_price(0.0) == "0"
