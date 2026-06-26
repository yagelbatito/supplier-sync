"""
Perla Home (perlahome.co.il) supplier scraper.

The site runs on Konimbo (an Israeli e-commerce platform). Two things to know:

1. The site rejects requests without a Referer header — it serves a
   `no_referer` redirect page instead. We always send a Referer.

2. Product listings are server-rendered (no JS needed). Each product on a
   category page is a `<div id="item_id_NNNN" class="layout_list_item ...">`
   block with the image, title, price and stock status inline.

Images are on CloudFront and unauthenticated. We pick the `/original/` path
for full-size images.
"""
import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.core.config_loader import SupplierConfig
from src.models.product import SupplierProduct
from src.scraping.http_client import HttpClient
from src.suppliers.base_supplier import BaseSupplier

BASE = "https://www.perlahome.co.il"

# Categories to skip entirely — sale/inactive landing pages that just duplicate
# real products under different URLs.
SKIP_CATEGORY_SLUGS = {"לא-פעיל", "מבצעים"}


class PerlahomeSupplier(BaseSupplier):
    def __init__(self, config: SupplierConfig, http: HttpClient):
        super().__init__(config, http)
        self._seen_ids: set[str] = set()

    # ── Public entry ─────────────────────────────────────────────

    def scrape(self) -> list[SupplierProduct]:
        cats = self._discover_categories()
        self.logger.info(f"Discovered {len(cats)} top-level categories")

        products: list[SupplierProduct] = []
        for cat_id, cat_name, cat_url in cats:
            try:
                before = len(products)
                for raw in self._iterate_category(cat_url):
                    p = self._parse_listing_item(raw, cat_name)
                    if p:
                        products.append(p)
                        if self.should_stop_scraping(products):
                            self.logger.info(
                                f"Reached scrape limit ({self.scrape_limit}), stopping early"
                            )
                            return products
                self.logger.info(f"  '{cat_name}': +{len(products) - before} new items")
            except Exception as exc:
                self.logger.warning(f"  Category '{cat_name}' failed: {exc}")
        return products

    # ── HTTP helpers ─────────────────────────────────────────────

    def _get_soup(self, url: str) -> BeautifulSoup:
        # Konimbo rejects requests without a Referer header — pass our own
        # base URL so it lets us through.
        resp = self.http.get(url, headers={"Referer": BASE + "/"})
        return BeautifulSoup(resp.text, "html.parser")

    # ── Category discovery ───────────────────────────────────────

    def _discover_categories(self) -> list[tuple[str, str, str]]:
        """
        Pull the list of top-level category links from the homepage.
        Returns [(numeric_id, name, url), ...].
        """
        soup = self._get_soup(BASE + "/")
        seen: set[str] = set()
        cats: list[tuple[str, str, str]] = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            m = re.match(r"^https?://(?:www\.)?perlahome\.co\.il/(\d{6,})-([^/?#]+)", href)
            if not m:
                continue
            cat_id = m.group(1)
            slug = m.group(2)
            if cat_id in seen or slug in SKIP_CATEGORY_SLUGS:
                continue
            seen.add(cat_id)
            # Name is preferably from the link text, fall back to slug.
            name = a.get_text(strip=True) or slug.replace("-", " ")
            cats.append((cat_id, name, href.split("?")[0]))
        return cats

    # ── Pagination ───────────────────────────────────────────────

    def _iterate_category(self, cat_url: str):
        """Yield <div> elements for each product on each page of a category."""
        page = 1
        while True:
            url = cat_url if page == 1 else f"{cat_url.rstrip('/')}?page={page}"
            try:
                soup = self._get_soup(url)
            except Exception as exc:
                self.logger.warning(f"  page {page} of {cat_url} failed: {exc}")
                break

            items = soup.select("div.layout_list_item[id^='item_id_']")
            if not items:
                break

            yielded_any = False
            for div in items:
                yielded_any = True
                yield div

            if not yielded_any:
                break

            # Defensive cap so a buggy site can't loop forever.
            page += 1
            if page > 200:
                self.logger.warning(f"  page cap reached for {cat_url}")
                break

    # ── Single-item parsing ──────────────────────────────────────

    def _parse_listing_item(self, div, category_name: str) -> Optional[SupplierProduct]:
        # Item id from the div id attribute
        div_id = div.get("id", "")
        m = re.match(r"item_id_(\d+)", div_id)
        if not m:
            return None
        item_id = m.group(1)
        if item_id in self._seen_ids:
            return None
        self._seen_ids.add(item_id)

        # Item code (SKU) — falls back to id if missing
        item_code = (div.get("data-item-code") or item_id).strip()

        # Title from .title
        title_el = div.select_one("h4.title") or div.select_one(".title")
        name = title_el.get_text(strip=True) if title_el else ""
        if not name:
            return None

        # Product URL — there are several <a> tags, all point to /items/ID-slug
        prod_link = ""
        for a in div.find_all("a", href=True):
            href = a["href"].strip()
            if "/items/" in href:
                prod_link = urljoin(BASE, href)
                break
        if not prod_link:
            prod_link = f"{BASE}/items/{item_id}"

        # Image — first <img> inside .img_wrapper. Prefer the /original/ variant
        # (much larger than the default /show/ thumbnail).
        img_el = div.select_one(".img_wrapper img") or div.select_one("img")
        images: list[str] = []
        if img_el:
            src = img_el.get("src") or img_el.get("data-src") or ""
            if src:
                # Konimbo CDN path /show/ → /original/ swap (larger image, same filename)
                full_src = re.sub(r"/system/photos/(\d+)/show/", r"/system/photos/\1/original/", src)
                images.append(full_src)

        # Price — text like "1,978 ₪"
        price = 0.0
        price_el = div.select_one("p.price")
        if price_el:
            price = self._parse_price(price_el.get_text(" ", strip=True))

        # Stock status
        stock_el = div.select_one("span.stock_state")
        stock_text = stock_el.get_text(strip=True).lower() if stock_el else ""
        if "in_stock" in stock_text or "instock" in stock_text:
            stock_status = "instock"
        elif "out" in stock_text or "no_stock" in stock_text:
            stock_status = "outofstock"
        else:
            stock_status = "instock"  # default to instock if not explicit

        return self._make_product(
            name=name,
            supplier_url=prod_link,
            price=price,
            images=images,
            stock_status=stock_status,
            supplier_product_id=item_code,
            supplier_category=category_name,
            original_description="",  # not present on listing; could be fetched per-product later
        )

    @staticmethod
    def _parse_price(text: str) -> float:
        # "1,978 ₪" or "1,978.00 ₪"
        if not text:
            return 0.0
        m = re.search(r"([\d,]+(?:\.\d+)?)", text)
        if not m:
            return 0.0
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return 0.0
