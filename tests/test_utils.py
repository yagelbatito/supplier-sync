"""Unit tests for core utils."""
import pytest

from src.core.utils import clean_price, stable_sku, truncate


class TestStableSku:
    def test_sku_with_product_id(self):
        sku = stable_sku("leopard", "12345", "https://example.com/product", "LPH")
        assert sku.startswith("LPH-")
        assert "12345" in sku

    def test_sku_without_product_id(self):
        sku = stable_sku("leopard", None, "https://leopardhome.com/כיסא-בר/", "LPH")
        assert sku.startswith("LPH-")
        assert len(sku) <= 60

    def test_same_url_same_sku(self):
        url = "https://leopardhome.com/product/bar-chair-123/"
        sku1 = stable_sku("leopard", None, url, "LPH")
        sku2 = stable_sku("leopard", None, url, "LPH")
        assert sku1 == sku2

    def test_different_urls_different_skus(self):
        sku1 = stable_sku("leopard", None, "https://example.com/product-a/", "LPH")
        sku2 = stable_sku("leopard", None, "https://example.com/product-b/", "LPH")
        assert sku1 != sku2

    def test_sku_max_length(self):
        long_id = "a" * 100
        sku = stable_sku("leopard", long_id, "https://example.com/", "LPH")
        assert len(sku) <= 60

    def test_sku_uppercase(self):
        sku = stable_sku("leopard", "abc", "https://example.com/", "lph")
        assert sku == sku.upper()


class TestCleanPrice:
    def test_basic_number(self):
        assert clean_price("1234") == pytest.approx(1234.0)

    def test_with_shekel(self):
        assert clean_price("₪ 1,234.50") == pytest.approx(1234.50)

    def test_empty_string(self):
        assert clean_price("") == 0.0

    def test_no_number(self):
        assert clean_price("אזל מהמלאי") == 0.0


class TestTruncate:
    def test_short_string_unchanged(self):
        assert truncate("hello", 20) == "hello"

    def test_long_string_truncated(self):
        result = truncate("a" * 100, 20)
        assert len(result) == 20
        assert result.endswith("...")

    def test_exact_length_unchanged(self):
        assert truncate("hello", 5) == "hello"
