"""Unit tests for config_loader."""
import os
from unittest.mock import patch

import pytest


class TestSupplierConfig:
    def test_load_suppliers_returns_dict(self):
        from src.core.config_loader import load_suppliers
        suppliers = load_suppliers()
        assert isinstance(suppliers, dict)
        assert len(suppliers) > 0

    def test_all_suppliers_have_required_fields(self):
        from src.core.config_loader import load_suppliers
        for key, cfg in load_suppliers().items():
            assert cfg.supplier_name, f"{key} missing supplier_name"
            assert 0 < cfg.price_multiplier <= 2.0, f"{key} invalid price_multiplier"
            assert cfg.sku_prefix, f"{key} missing sku_prefix"

    def test_enabled_suppliers(self):
        from src.core.config_loader import load_enabled_suppliers
        enabled = load_enabled_suppliers()
        # All returned suppliers should be enabled
        for key, cfg in enabled.items():
            assert cfg.enabled, f"{key} should be enabled"


class TestCategoryMapping:
    def test_load_category_mapping(self):
        from src.core.config_loader import load_category_mapping
        mapping = load_category_mapping()
        assert isinstance(mapping, dict)
        assert "leopard" in mapping

    def test_load_keyword_rules(self):
        from src.core.config_loader import load_keyword_rules
        rules = load_keyword_rules()
        assert isinstance(rules, dict)
        assert "ספות" in rules


class TestAppSettings:
    def test_missing_wc_url_raises(self):
        from src.core.config_loader import load_app_settings
        from src.core.exceptions import ConfigurationError
        with patch.dict(os.environ, {
            "WOOCOMMERCE_URL": "",
            "WOOCOMMERCE_KEY": "test",
            "WOOCOMMERCE_SECRET": "test",
        }):
            with pytest.raises(ConfigurationError):
                load_app_settings()

    def test_dry_run_from_env(self):
        from src.core.config_loader import load_app_settings
        with patch.dict(os.environ, {
            "WOOCOMMERCE_URL": "https://test.co.il",
            "WOOCOMMERCE_KEY": "ck_test",
            "WOOCOMMERCE_SECRET": "cs_test",
            "DRY_RUN": "true",
        }):
            settings = load_app_settings()
            assert settings.dry_run is True
