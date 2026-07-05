"""
Home21 supplier scraper.
Based on the existing import_home21.py, refactored for the modular architecture.
"""
from typing import Optional

from src.core.config_loader import SupplierConfig
from src.models.product import SupplierProduct
from src.scraping.http_client import HttpClient
from src.scraping.parser_utils import find_images, find_price
from src.suppliers.base_supplier import BaseSupplier

BASE = "https://home21.co.il"

CATEGORY_URLS = [
    ("כיסאות בר", f"{BASE}/%D7%9B%D7%A1%D7%90%D7%95%D7%AA-%D7%91%D7%A8/"),
    ("כיסאות אוכל", f"{BASE}/%D7%9B%D7%99%D7%A1%D7%90%D7%95%D7%AA-%D7%90%D7%95%D7%9B%D7%9C/"),
    ("שולחנות אוכל", f"{BASE}/%D7%A9%D7%95%D7%9C%D7%97%D7%A0%D7%95%D7%AA-%D7%90%D7%95%D7%9B%D7%9C/"),
]


class Home21Supplier(BaseSupplier):
    def __init__(self, config: SupplierConfig, http: HttpClient):
        super().__init__(config, http)
        self._seen: set[str] = set()

    def scrape(self) -> list[SupplierProduct]:
        products: list[SupplierProduct] = []

        for cat_name, cat_url in CATEGORY_URLS:
            try:
                url_name_pairs = self._collect_product_urls(cat_url)
                self.logger.info(f"  '{cat_name}': {len(url_name_pairs)} products")

                for url, _name in url_name_pairs:
                    if url in self._seen:
                        continue
                    self._seen.add(url)
                    try:
                        p = self._parse_product(url, cat_name)
                        if p:
                            products.append(p)
                            if self.should_stop_scraping(products):
                                self.logger.info(
                                    f"Reached scrape limit ({self.scrape_limit}), stopping early"
                                )
                                return products
                    except Exception as exc:
                        self.logger.warning(f"  Failed {url}: {exc}")

            except Exception as exc:
                self.logger.error(f"  Category '{cat_name}' failed: {exc}")

        return products

    def _collect_product_urls(self, category_url: str) -> list[tuple[str, str]]:
        """Collect (url, name) pairs from category page."""
        soup = self.http.get_soup(category_url)
        items = soup.find_all("li", class_="productItem")
        pairs = []

        for li in items:
            link = li.find("a", class_="linkTo")
            name_el = li.select_one("div.name h2")
            if not link or not link.get("href"):
                continue
            url = link["href"]
            if url.startswith("/"):
                url = BASE + url
            name = name_el.get_text(strip=True) if name_el else ""
            pairs.append((url, name))

        return pairs

    def _parse_product(self, url: str, category: str) -> Optional[SupplierProduct]:
        soup = self.http.get_soup(url)

        name_el = soup.find("h1", itemprop="name")
        if not name_el:
            return None
        name = name_el.get_text(strip=True)

        price = find_price(soup)
        images = find_images(soup, BASE)

        desc_el = soup.find("div", class_="description")
        desc = desc_el.get_text("\n", strip=True) if desc_el else ""

        # Colors
        colors: list[str] = []
        color_label = soup.find(string=lambda t: t and "צבע" in t)
        if color_label:
            parent = color_label.parent
            for el in parent.find_all_next(limit=10):
                txt = el.get_text(" ", strip=True)
                if not txt or "₪" in txt or "מחיר" in txt:
                    break
                clean = txt.replace("צבעים", "").strip()
                if clean and len(clean) < 30 and clean not in colors:
                    colors.append(clean)

        slug = url.rstrip("/").split("/")[-1]
        # home21 renders a hidden <div class="outOfStock hide">אזל מהמלאי</div>
        # on EVERY product page — the "hide" class is dropped only when the
        # item is truly sold out. Scanning page text for "אזל" therefore marked
        # every product out of stock. Check whether that div is actually shown.
        oos_div = soup.find("div", class_="outOfStock")
        is_oos = bool(oos_div) and "hide" not in (oos_div.get("class") or [])
        stock = "outofstock" if is_oos else "instock"

        p = self._make_product(
            name=name,
            supplier_url=url,
            price=price,
            images=images,
            stock_status=stock,
            supplier_product_id=slug,
            supplier_category=category,
            original_description=desc,
            color=", ".join(colors) if colors else "",
        )
        return p
