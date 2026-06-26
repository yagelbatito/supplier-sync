"""
Omed 21 supplier scraper.
Uses Selenium for JavaScript-heavy pages.
Falls back to requests if Selenium is unavailable.
"""
from typing import Optional

from src.core.config_loader import SupplierConfig
from src.models.product import SupplierProduct
from src.scraping.http_client import HttpClient
from src.scraping.parser_utils import extract_attribute, find_images, find_price
from src.suppliers.base_supplier import BaseSupplier

BASE = "https://www.omed21.co.il"

CATEGORY_URLS = [
    ("ספות", f"{BASE}/ספות/"),
    ("כורסאות", f"{BASE}/כורסאות/"),
    ("פינות אוכל", f"{BASE}/פינות-אוכל/"),
    ("מיטות", f"{BASE}/מיטות/"),
]


class Omed21Supplier(BaseSupplier):
    def __init__(self, config: SupplierConfig, http: HttpClient):
        super().__init__(config, http)
        self._driver = None
        self._seen: set[str] = set()

    def scrape(self) -> list[SupplierProduct]:
        try:
            self._driver = self._init_selenium()
            self.logger.info("Selenium driver initialized")
        except Exception as exc:
            self.logger.warning(f"Selenium unavailable ({exc}), falling back to requests")
            self._driver = None

        products: list[SupplierProduct] = []

        for cat_name, cat_url in CATEGORY_URLS:
            try:
                links = self._collect_links(cat_url)
                for url in links:
                    if url in self._seen:
                        continue
                    self._seen.add(url)
                    try:
                        p = self._parse_product(url, cat_name)
                        if p:
                            products.append(p)
                    except Exception as exc:
                        self.logger.warning(f"Parse failed {url}: {exc}")
            except Exception as exc:
                self.logger.error(f"Category '{cat_name}' failed: {exc}")

        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass

        return products

    def _init_selenium(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        opts = Options()
        opts.add_argument("--headless")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        return webdriver.Chrome(options=opts)

    def _get_soup_via_selenium(self, url: str):
        import time

        from bs4 import BeautifulSoup
        self._driver.get(url)
        time.sleep(2)
        return BeautifulSoup(self._driver.page_source, "html.parser")

    def _collect_links(self, category_url: str) -> list[str]:
        links = []
        try:
            if self._driver:
                soup = self._get_soup_via_selenium(category_url)
            else:
                soup = self.http.get_soup(category_url)

            for a in soup.select("a[href]"):
                href = a.get("href", "")
                if href.startswith("/"):
                    href = BASE + href
                if href.startswith(BASE) and href.count("/") >= 4 and href not in links:
                    links.append(href)
        except Exception as exc:
            self.logger.warning(f"Link collection failed for {category_url}: {exc}")
        return links

    def _parse_product(self, url: str, category: str) -> Optional[SupplierProduct]:
        if self._driver:
            soup = self._get_soup_via_selenium(url)
        else:
            soup = self.http.get_soup(url)

        name_el = soup.find("h1")
        if not name_el:
            return None
        name = name_el.get_text(strip=True)

        price = find_price(soup)
        images = find_images(soup, BASE)
        desc_el = soup.find("div", class_="description") or soup.find("div", id="description")
        desc = desc_el.get_text("\n", strip=True) if desc_el else ""

        material = extract_attribute(soup, ["חומר", "material"])
        color = extract_attribute(soup, ["צבע", "color"])
        height = extract_attribute(soup, ["גובה", "height"])

        slug = url.rstrip("/").split("/")[-1]
        stock_text = soup.get_text().lower()
        stock = "outofstock" if any(w in stock_text for w in ["אזל", "out of stock"]) else "instock"

        return self._make_product(
            name=name,
            supplier_url=url,
            price=price,
            images=images,
            stock_status=stock,
            supplier_product_id=slug,
            supplier_category=category,
            original_description=desc,
            material=material,
            color=color,
            height=height,
        )
