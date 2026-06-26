"""
Leopard Home supplier scraper.
Scrapes product listings and individual product pages from leopardhome.com.
Based on the existing IMPORT_LEOPARD_V.py, refactored for the modular architecture.
"""
import re
from typing import Optional

from bs4 import BeautifulSoup

from src.core.config_loader import SupplierConfig
from src.models.product import SupplierProduct
from src.scraping.http_client import HttpClient
from src.scraping.parser_utils import extract_attribute, find_images, find_price
from src.suppliers.base_supplier import BaseSupplier

# Categories to scrape — extend this list freely
CATEGORY_URLS = [
    ("כיסאות בר", "https://www.leopardhome.com/%D7%9B%D7%A1%D7%90%D7%95%D7%AA-%D7%91%D7%A8/"),
    ("שולחנות סלון", "https://www.leopardhome.com/%D7%A9%D7%95%D7%9C%D7%97%D7%A0%D7%95%D7%AA-%D7%A1%D7%9C%D7%95%D7%9F/"),
    ("פינות אוכל", "https://www.leopardhome.com/%D7%A4%D7%99%D7%A0%D7%95%D7%AA-%D7%90%D7%95%D7%9B%D7%9C/"),
    ("ספות", "https://www.leopardhome.com/%D7%A1%D7%A4%D7%95%D7%AA/"),
    ("כורסאות", "https://www.leopardhome.com/%D7%9B%D7%95%D7%A8%D7%A1%D7%90%D7%95%D7%AA/"),
]

BASE = "https://www.leopardhome.com"
BRAND_NAMES = ["לאופרד", "Leopard", "LEOPARD", "leopardhome", "leopard home"]


class LeopardSupplier(BaseSupplier):
    def __init__(self, config: SupplierConfig, http: HttpClient):
        super().__init__(config, http)
        self._seen_urls: set[str] = set()

    def scrape(self) -> list[SupplierProduct]:
        all_products: list[SupplierProduct] = []

        for category_name, category_url in CATEGORY_URLS:
            try:
                links = self._collect_product_links(category_url)
                self.logger.info(f"  Category '{category_name}': {len(links)} products")

                for url in links:
                    if url in self._seen_urls:
                        continue
                    self._seen_urls.add(url)
                    try:
                        product = self._parse_product(url, category_name)
                        if product:
                            all_products.append(product)
                            if self.should_stop_scraping(all_products):
                                self.logger.info(
                                    f"Reached scrape limit ({self.scrape_limit}), stopping early"
                                )
                                return all_products
                    except Exception as exc:
                        self.logger.warning(f"  Failed to parse {url}: {exc}")

            except Exception as exc:
                self.logger.error(f"  Failed to scrape category '{category_name}': {exc}")

        return all_products

    def _collect_product_links(self, category_url: str) -> list[str]:
        """Collect all product page URLs from a paginated category."""
        links: list[str] = []
        next_page: Optional[str] = category_url

        while next_page:
            try:
                soup = self.http.get_soup(next_page)
            except Exception:
                break

            # Limit link extraction to the products listing block — the
            # sidebar/menu has category links with the same URL shape, so we'd
            # otherwise pick up parents like /כסאות/ as if they were products.
            # leopardhome's theme uses `productsList` (camelCase) on the <ul>.
            containers = soup.select(
                "ul.productsList, "
                "ul.Products, "
                "ul.products, "
                "div.productsList, "
                "div.products, "
                "div.elementor-products-grid"
            )
            scope = containers if containers else [soup]

            for cont in scope:
                for a in cont.select("a[href]"):
                    href = a.get("href", "")
                    if href.startswith("/"):
                        href = BASE + href
                    if not href.startswith(BASE):
                        continue
                    if self._is_product_url(href) and href not in links:
                        links.append(href)

            # Pagination
            next_page = self._find_next_page(soup, next_page)

        return links

    def _is_product_url(self, url: str) -> bool:
        skip = ["/אודות", "/שירות-לקוחות", "/צור-קשר", "/תקנון", "/מדיניות",
                "/בלוג", "/קטלוג", "/collections", "/category", "/מוצרים-חדשים", "/מבצעים"]
        return url.count("/") >= 4 and not any(x in url for x in skip)

    def _find_next_page(self, soup: BeautifulSoup, current: str) -> Optional[str]:
        next_btn = soup.select_one("a.next, a[rel='next'], .pagination .next a")
        if next_btn:
            href = next_btn.get("href", "")
            if href.startswith("/"):
                href = BASE + href
            if href and href != current:
                return href
        return None

    def _parse_product(self, url: str, category_name: str) -> Optional[SupplierProduct]:
        soup = self.http.get_soup(url)

        # Name
        name_el = soup.find("h1") or soup.find("h1", itemprop="name")
        if not name_el:
            return None
        name = self._clean_name(name_el.get_text(strip=True))
        if not name:
            return None

        # Price
        price = find_price(soup)

        # Stock
        stock_status = "instock"
        out_indicators = ["אזל", "אזל מהמלאי", "out of stock", "outofstock"]
        page_text = soup.get_text().lower()
        if any(ind in page_text for ind in out_indicators):
            stock_status = "outofstock"

        # Description
        desc_el = soup.find("div", class_="description") or soup.find(
            "div", itemprop="description"
        )
        desc = desc_el.get_text("\n", strip=True) if desc_el else ""

        # Attributes
        material = extract_attribute(soup, ["חומר", "material", "Material"])
        color = extract_attribute(soup, ["צבע", "color", "Color"])
        width = extract_attribute(soup, ["רוחב", "W", "width"])
        depth = extract_attribute(soup, ["עומק", "D", "depth"])
        height = extract_attribute(soup, ["גובה", "H", "height"])

        # Images
        images = find_images(soup, BASE)

        if not images:
            self.logger.warning(f"No images found for {url}")

        # Product ID from URL slug
        slug = url.rstrip("/").split("/")[-1]

        return self._make_product(
            name=name,
            supplier_url=url,
            price=price,
            images=images,
            stock_status=stock_status,
            supplier_product_id=slug,
            supplier_category=category_name,
            original_description=desc,
            material=material,
            color=color,
            width=width,
            depth=depth,
            height=height,
        )

    def _clean_name(self, name: str) -> str:
        """Remove brand names and clean up product name."""
        result = name
        for brand in BRAND_NAMES:
            result = re.sub(re.escape(brand), "", result, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", result).strip(" -|")
