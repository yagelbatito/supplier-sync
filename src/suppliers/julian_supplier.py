"""
Golyan (Julian) supplier scraper.

Golyan's WordPress site (mydgolyan.com) is just a shell — the actual catalog
is rendered client-side by WizShop (shop4.wizsoft.com). We bypass the page
entirely and call the WizShop JSON API directly.

API endpoints:
  - HTTPREQ=14 + VSTree=Yes  → category tree
  - HTTPREQ=8  + ItmCategory → items in a category (leaf name only)

Response quirk: the body comes back as JSON with each character encoded as
`%uXXXX` (JavaScript-style unicode escapes). We decode it before json.loads.
Request quirk: query parameters with Hebrew chars must use WizShop's custom
single-byte encoding (Hebrew aleph → 0xE0, etc.), NOT UTF-8.
"""
import json
import re
import urllib.parse
from typing import Optional

from src.core.config_loader import SupplierConfig
from src.models.product import SupplierProduct
from src.scraping.http_client import HttpClient
from src.suppliers.base_supplier import BaseSupplier

BASE = "https://www.mydgolyan.com"
API_BASE = "https://shop4.wizsoft.com/vshop/WSHOP.wzx"
UC = "golyangifts"
LANG = "HE"

# Product images are stored on the WordPress media library, NOT on WizShop's
# server. WizShop's own VSImagePath (shop4.wizsoft.com/.../golyangiftsImg/heb/)
# returns 404 — those filenames are only metadata. The actual full-size image
# is at `<wp-site>/wp-content/uploads/wizshop/{Key}.jpg`, and WP also creates
# resized variants like `-300x300.jpg`. We always pull the full version.
IMAGE_BASE = f"{BASE}/wp-content/uploads/wizshop"

# mydgolyan.com blocks any UA starting with "WordPress/*" with 403 Forbidden,
# which is exactly the UA WooCommerce uses when fetching remote image URLs
# from a product payload. We route through wsrv.nl, a public image proxy that
# fetches with a browser UA and serves via Cloudflare. The target WC site
# fetches from wsrv.nl with no UA gating involved.
IMAGE_PROXY = "https://wsrv.nl/?url="

# Categories that aren't real products (skip)
SKIP_CATEGORY_NAMES = {"NON", "", "ראש הדף"}


class JulianSupplier(BaseSupplier):
    def __init__(self, config: SupplierConfig, http: HttpClient):
        super().__init__(config, http)
        self._seen_keys: set[str] = set()

    # ─────────────────────────────────────────────────────────────
    # Public entry
    # ─────────────────────────────────────────────────────────────

    def scrape(self) -> list[SupplierProduct]:
        leaf_categories = self._fetch_leaf_categories()
        self.logger.info(f"WizShop returned {len(leaf_categories)} leaf categories")

        products: list[SupplierProduct] = []
        for cat_name, parent_path in leaf_categories:
            try:
                items, columns = self._fetch_items(cat_name)
                self.logger.info(f"  '{cat_name}': {len(items)} items")
                for raw in items:
                    p = self._parse_item(raw, columns, cat_name, parent_path)
                    if p:
                        products.append(p)
                        if self.should_stop_scraping(products):
                            self.logger.info(
                                f"Reached scrape limit ({self.scrape_limit}), stopping early"
                            )
                            return products
            except Exception as exc:
                self.logger.warning(f"  Category '{cat_name}' failed: {exc}")
        return products

    # ─────────────────────────────────────────────────────────────
    # WizShop API plumbing
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def _heb_encode(s: str) -> str:
        """
        WizShop's custom query encoder: Hebrew chars are mapped to single bytes
        starting at 0xE0 (windows-1255 / iso-8859-8 style), then percent-encoded.
        Non-Hebrew chars go through normal URL quoting.
        """
        out = []
        for ch in s:
            code = ord(ch)
            if code >= 0x05D0:  # Hebrew aleph and onward
                out.append(f"%{(0xE0 + (code - 0x05D0)) & 0xFF:02x}")
            else:
                out.append(urllib.parse.quote(ch, safe=""))
        return "".join(out)

    @classmethod
    def _build_url(cls, params: dict) -> str:
        qs = "&".join(f"{k}={cls._heb_encode(str(v))}" for k, v in params.items())
        return f"{API_BASE}?{qs}"

    def _api_get(self, params: dict) -> dict:
        url = self._build_url(params)
        resp = self.http.get(url, headers={"Accept": "*/*"})
        raw = resp.text
        # Each char is `%uXXXX` — convert to real chars before parsing JSON.
        decoded = re.sub(r"%u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), raw)
        return json.loads(decoded)

    # ─────────────────────────────────────────────────────────────
    # Categories
    # ─────────────────────────────────────────────────────────────

    def _fetch_leaf_categories(self) -> list[tuple[str, str]]:
        """
        Walk the category tree, return [(leaf_name, parent_path), ...].
        We only want categories that have no children — those are the ones
        you can query GetItemsForCat against.
        """
        data = self._api_get({
            "Lang": LANG, "API": "Yes", "HTTPREQ": "14", "UC": UC, "VSTree": "Yes",
        })
        leaves: list[tuple[str, str]] = []
        for top in data.get("OutTab", []):
            self._walk_categories(top, parent_path="", out=leaves)
        # de-dupe and skip empty
        seen: set[str] = set()
        unique: list[tuple[str, str]] = []
        for name, path in leaves:
            if name in SKIP_CATEGORY_NAMES or name in seen:
                continue
            seen.add(name)
            unique.append((name, path))
        return unique

    def _walk_categories(self, node, parent_path: str, out: list[tuple[str, str]]) -> None:
        """
        Each node: [SonName, ESonName, FullPath, EFullPath, ParentName, DispType,
                    Icon, DispOrder, VSCatalogMode, VSInfoEx, SubCatList]
        SubCatList = [child_node, child_node, ...] (empty when leaf)
        """
        if not isinstance(node, list) or len(node) < 11:
            return
        name = node[0]
        sub_list = node[10]
        new_path = f"{parent_path}@@{name}" if parent_path else name

        # Heuristic: if SubCatList contains real category sub-arrays, recurse.
        # Otherwise this node is a leaf — record it.
        has_children = (
            isinstance(sub_list, list)
            and sub_list
            and all(isinstance(c, list) and len(c) >= 3 for c in sub_list)
        )
        if has_children:
            for child in sub_list:
                self._walk_categories(child, new_path, out)
        else:
            if name not in SKIP_CATEGORY_NAMES:
                out.append((name, parent_path))

    # ─────────────────────────────────────────────────────────────
    # Items
    # ─────────────────────────────────────────────────────────────

    def _fetch_items(self, category_name: str) -> tuple[list[list], list[str]]:
        """
        Get items for a single leaf category. Uses VSGetImgEx so the image
        list is included.
        """
        all_rows: list[list] = []
        page_size = 500
        start = 0
        columns: list[str] = []
        while True:
            data = self._api_get({
                "Lang": LANG, "API": "Yes", "HTTPREQ": "8", "UC": UC,
                "ItmCategory": category_name,
                "VSMaxItms": str(page_size),
                "ItemsRangeFrom": str(start),
                "VSGetImgEx": "Yes",
            })
            rows = data.get("OutTab", []) or []
            if not columns:
                columns = data.get("Columns", [])
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
            start += len(rows)
            if start > 10000:  # paranoid cap
                self.logger.warning(f"  Category '{category_name}' exceeded 10k items, stopping pagination")
                break
        return all_rows, columns

    def _parse_item(
        self,
        row: list,
        columns: list[str],
        category_name: str,
        parent_path: str,
    ) -> Optional[SupplierProduct]:
        if not row or not columns:
            return None

        def col(name: str, default=""):
            try:
                idx = columns.index(name)
            except ValueError:
                return default
            return row[idx] if idx < len(row) else default

        key = str(col("Key", "")).strip()
        name = str(col("Name", "")).strip()
        if not key or not name:
            return None

        # WizShop sometimes lists the same item under multiple categories.
        if key in self._seen_keys:
            return None
        self._seen_keys.add(key)

        # Price — "700.00 ש"ח" → 700.0
        price = self._parse_price(col("FormatedPrice"))
        if not price:
            try:
                price = float(col("FullPrice") or 0)
            except (TypeError, ValueError):
                price = 0.0

        # Stock
        try:
            balance = float(col("Balance") or 0)
        except (TypeError, ValueError):
            balance = 0
        # Stock is driven by Balance alone. CanBePurchased is '0' even for items
        # with hundreds in stock (it flags something else, not availability), so
        # AND-ing it wrongly marked every product out of stock.
        stock_status = "instock" if balance > 0 else "outofstock"

        # Images live on the WordPress media library at
        # `wp-content/uploads/wizshop/{Key}.jpg`. The WizShop API's
        # ImageFile/BigImageFile values are usually `{Key}.jpg` already, but
        # we derive from Key to be safe regardless of how WizShop names them.
        # Wrap via wsrv.nl so the target WC site can fetch without hitting
        # mydgolyan's WordPress-UA block.
        images: list[str] = []
        origin = f"{IMAGE_BASE}/{key}.jpg"
        # wsrv.nl expects URL without protocol (it adds https itself)
        proxied = f"{IMAGE_PROXY}{origin.replace('https://', '').replace('http://', '')}"
        images.append(proxied)

        description = str(col("Description") or "")

        # The WordPress shopfront uses a hash fragment to deep-link items.
        # That's good enough as a stable supplier_url for our matcher.
        supplier_url = f"{BASE}/#item={urllib.parse.quote(key)}"

        return self._make_product(
            name=name,
            supplier_url=supplier_url,
            price=price,
            images=images,
            stock_status=stock_status,
            supplier_product_id=key,
            supplier_category=category_name,
            original_description=description,
        )

    @staticmethod
    def _parse_price(text) -> float:
        if not text:
            return 0.0
        # Extract first numeric run, allow comma as thousands sep.
        m = re.search(r"([\d,]+(?:\.\d+)?)", str(text))
        if not m:
            return 0.0
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return 0.0
