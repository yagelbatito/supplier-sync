"""
CSV / Google Sheets supplier.
Allows importing products from a local CSV file or a public Google Sheets URL.

Column mapping (case-insensitive, flexible):
  name / שם מוצר
  price / מחיר
  description / תיאור
  category / קטגוריה
  image_url / תמונה
  sku / מק"ט
  stock_status / מלאי
  url / קישור
  material / חומר
  color / צבע
  width / רוחב
  depth / עומק
  height / גובה

Usage:
  python -m src.main --supplier csv --csv-file /path/to/products.csv
  python -m src.main --supplier csv --sheets-url "https://docs.google.com/spreadsheets/d/SHEET_ID/export?format=csv"
"""
import csv
import io
from pathlib import Path
from typing import Optional

import requests

from src.core.config_loader import SupplierConfig
from src.core.logger import get_logger
from src.models.product import SupplierProduct
from src.scraping.http_client import HttpClient
from src.suppliers.base_supplier import BaseSupplier

logger = get_logger(__name__)

# Flexible column name aliases (lowercase)
COLUMN_ALIASES: dict[str, list[str]] = {
    "name":         ["name", "שם מוצר", "שם", "product name", "title"],
    "price":        ["price", "מחיר", "מחיר ספק", "supplier price"],
    "description":  ["description", "תיאור", "תיאור מוצר"],
    "category":     ["category", "קטגוריה", "category_name"],
    "image_url":    ["image_url", "תמונה", "image", "images", "image url", "url תמונה"],
    "sku":          ["sku", "מק\"ט", "מקט", "product id"],
    "stock_status": ["stock_status", "מלאי", "availability", "stock"],
    "url":          ["url", "קישור", "link", "product url", "קישור מוצר"],
    "material":     ["material", "חומר"],
    "color":        ["color", "צבע"],
    "width":        ["width", "רוחב"],
    "depth":        ["depth", "עומק"],
    "height":       ["height", "גובה"],
}


class CsvSupplier(BaseSupplier):
    """
    Import products from a CSV file or a Google Sheets public export URL.
    Can be used as a generic source for any ad-hoc imports.
    """

    def __init__(
        self,
        config: SupplierConfig,
        http: HttpClient,
        csv_path: Optional[str] = None,
        sheets_url: Optional[str] = None,
    ):
        super().__init__(config, http)
        self.csv_path = csv_path
        self.sheets_url = sheets_url

    def scrape(self) -> list[SupplierProduct]:
        rows = self._load_rows()
        if not rows:
            self.logger.warning("CSV: No rows loaded")
            return []

        self.logger.info(f"CSV: Loaded {len(rows)} rows")
        products = []

        for i, row in enumerate(rows, 1):
            try:
                p = self._parse_row(row, i)
                if p:
                    products.append(p)
            except Exception as exc:
                self.logger.warning(f"CSV row {i} failed: {exc}")

        return products

    def _load_rows(self) -> list[dict]:
        if self.csv_path:
            return self._load_from_file(self.csv_path)
        if self.sheets_url:
            return self._load_from_url(self.sheets_url)

        # Try env vars as fallback
        import os
        path = os.getenv("INPUT_CSV", "")
        if path and Path(path).exists():
            return self._load_from_file(path)

        self.logger.error("CSV: No source specified (csv_path, sheets_url, or INPUT_CSV env)")
        return []

    def _load_from_file(self, path: str) -> list[dict]:
        with open(path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def _load_from_url(self, url: str) -> list[dict]:
        """Load from Google Sheets export URL or any CSV URL."""
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        content = resp.content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))
        return list(reader)

    def _parse_row(self, row: dict, row_num: int) -> Optional[SupplierProduct]:
        """Map flexible column names to SupplierProduct fields."""
        norm = {k.strip().lower(): v.strip() for k, v in row.items() if v}

        def get(field: str, default: str = "") -> str:
            for alias in COLUMN_ALIASES.get(field, [field]):
                if alias in norm:
                    return norm[alias]
            return default

        name = get("name")
        if not name:
            self.logger.debug(f"Row {row_num}: skipped (no name)")
            return None

        # Price
        price_str = get("price", "0")
        try:
            price = float(price_str.replace(",", "").replace("₪", "").strip() or "0")
        except ValueError:
            price = 0.0

        # URL — use SKU-based or generate from name
        url = get("url") or f"{self.config.base_url}/products/{name.replace(' ', '-')}"

        # Images — comma separated allowed
        images_raw = get("image_url")
        images = [u.strip() for u in images_raw.split(",") if u.strip()] if images_raw else []

        # Stock
        stock_raw = get("stock_status", "instock").lower()
        stock = "instock" if stock_raw in ("instock", "in stock", "1", "yes", "כן", "במלאי", "") else "outofstock"

        sku_override = get("sku")

        from src.core.utils import stable_sku
        sku = (
            f"{self.config.sku_prefix}-{sku_override}".upper()
            if sku_override
            else stable_sku(self.config.key, sku_override or None, url, self.config.sku_prefix)
        )

        return SupplierProduct(
            supplier_name=self.config.supplier_name,
            supplier_key=self.config.key,
            supplier_product_id=sku_override or None,
            supplier_url=url,
            sku=sku,
            name=name,
            original_description=get("description"),
            price=price,
            stock_status=stock,
            is_available=stock == "instock",
            supplier_category=get("category"),
            images=images,
            material=get("material"),
            color=get("color"),
            width=get("width"),
            depth=get("depth"),
            height=get("height"),
        )
