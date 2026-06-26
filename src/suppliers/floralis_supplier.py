"""
Floralis supplier scraper.
Floralis runs on Shopify, so products are available via /products.json.

The JSON endpoint gives us basic info (name, price, images, description) but
NOT the structured attribute accordions that appear on the live product page
("מידה", "עשוי מ..", "צבע", "הוראות שימוש"). Those live in metafields that
aren't exposed in /products.json, so we scrape the product page for each
item to extract them. The cost is one extra HTTP call per product, paid
during scrape — done before any OpenAI call so the AI prompt sees richer
input and weaves these details into the generated description.
"""
import re
from typing import Optional

from src.core.config_loader import SupplierConfig
from src.models.product import SupplierProduct
from src.scraping.http_client import HttpClient
from src.scraping.parser_utils import find_images, find_price
from src.suppliers.base_supplier import BaseSupplier

BASE = "https://www.floralis.co.il"
PRODUCTS_JSON = f"{BASE}/products.json"

# Maps the Hebrew accordion title on the product page to a SupplierProduct field.
# The "עשוי מ.." label has a real two-dot suffix on the live site — match both
# variants just in case Floralis tweaks the punctuation.
_ACCORDION_KEYS_MATERIAL = ("עשוי מ..", "עשוי מ.", "עשוי מ", "חומר")
_ACCORDION_KEYS_COLOR = ("צבע",)
_ACCORDION_KEYS_SIZE = ("מידה", "מידות")
_ACCORDION_KEYS_USAGE = ("הוראות שימוש",)


class FloralisSupplier(BaseSupplier):
    """
    Floralis is Shopify-based. We use the /products.json endpoint
    which gives clean JSON without scraping HTML.
    Fallback to HTML scraping if JSON is not available.
    """

    def __init__(self, config: SupplierConfig, http: HttpClient):
        super().__init__(config, http)

    def scrape(self) -> list[SupplierProduct]:
        products = self._scrape_via_json()
        if not products:
            self.logger.warning("JSON scrape returned nothing, falling back to HTML")
            products = self._scrape_via_html()
        return products

    # ── Strategy 1: Shopify JSON API ─────────────────────────────

    def _scrape_via_json(self) -> list[SupplierProduct]:
        products: list[SupplierProduct] = []
        page = 1

        while True:
            url = f"{PRODUCTS_JSON}?limit=250&page={page}"
            try:
                resp = self.http.get(url, headers={"Accept": "application/json"})
                data = resp.json()
            except Exception as exc:
                self.logger.warning(f"JSON page {page} failed: {exc}")
                # If we already collected products from earlier pages and the next
                # page fails, the scrape is partial — drafting "missing" managed
                # products would hide live items. The HTML fallback is only used
                # when we got nothing at all, so flag incomplete here.
                if products:
                    self.mark_scrape_incomplete(f"JSON page {page} failed: {exc}")
                break

            raw_products = data.get("products", [])
            if not raw_products:
                break

            self.logger.info(
                f"Fetched JSON page {page} ({len(raw_products)} products) "
                f"— now fetching per-product attributes"
            )

            for raw in raw_products:
                p = self._parse_json_product(raw)
                if p:
                    products.append(p)
                    if len(products) % 25 == 0:
                        self.logger.info(f"Scraped {len(products)} products so far")
                    if self.should_stop_scraping(products):
                        self.logger.info(
                            f"Reached scrape limit ({self.scrape_limit}), stopping early"
                        )
                        return products

            if len(raw_products) < 250:
                break
            page += 1

        self.logger.info(f"Floralis scrape finished: {len(products)} products")
        return products

    def _parse_json_product(self, raw: dict) -> Optional[SupplierProduct]:
        name = raw.get("title", "").strip()
        if not name:
            return None

        handle = raw.get("handle", "")
        product_url = f"{BASE}/products/{handle}"
        product_id = str(raw.get("id", ""))

        # Use first available variant price
        variants = raw.get("variants", [])
        price = 0.0
        stock_status = "outofstock"
        if variants:
            v = variants[0]
            try:
                price = float(v.get("price", 0) or 0)
            except (ValueError, TypeError):
                price = 0.0
            if v.get("available", False):
                stock_status = "instock"

        # Category from product_type
        category = raw.get("product_type", "")

        # Images
        images = [img["src"] for img in raw.get("images", []) if img.get("src")]

        # Description — strip HTML
        body = raw.get("body_html", "") or ""
        desc = re.sub(r"<[^>]+>", " ", body).strip()
        desc = re.sub(r"\s+", " ", desc)

        # Enrich with the accordions on the product page: מידה / עשוי מ.. / צבע / הוראות שימוש.
        # Failures (timeouts, layout changes) downgrade silently — we still want the JSON-derived
        # product, just without the extras.
        attrs: dict[str, str] = {}
        try:
            attrs = self._fetch_page_attrs(product_url)
        except Exception as exc:
            self.logger.debug(f"page attr fetch failed for {handle}: {exc}")

        material = self._first(attrs, _ACCORDION_KEYS_MATERIAL)
        color = self._first(attrs, _ACCORDION_KEYS_COLOR)
        size_text = self._first(attrs, _ACCORDION_KEYS_SIZE)
        usage = self._first(attrs, _ACCORDION_KEYS_USAGE)

        width = self._extract_dimension(size_text, ("רוחב",))
        height = self._extract_dimension(size_text, ("גובה",))
        depth = self._extract_dimension(size_text, ("עומק", "אורך"))

        # The "הוראות שימוש" content isn't a SupplierProduct field on its own,
        # so we splice it into the description. The AI prompt reads description
        # and will weave usage instructions into the generated copy.
        if usage:
            desc = (desc + "\n\nהוראות שימוש: " + usage).strip()

        return self._make_product(
            name=name,
            supplier_url=product_url,
            price=price,
            images=images,
            stock_status=stock_status,
            supplier_product_id=product_id,
            supplier_category=category,
            original_description=desc,
            material=material,
            color=color,
            width=width,
            height=height,
            depth=depth,
            dimensions=size_text,
        )

    # ── Product-page attribute extraction ────────────────────────

    def _fetch_page_attrs(self, product_url: str) -> dict[str, str]:
        """Return {accordion_title: content_text} for the 4 attribute blocks."""
        soup = self.http.get_soup(product_url)
        out: dict[str, str] = {}
        for title_el in soup.select("h2.accordion__title, h3.accordion__title"):
            label = title_el.get_text(strip=True)
            if not label:
                continue
            details = title_el.find_parent("details")
            if not details:
                continue
            # Pull text from the details body, excluding the summary (which is
            # the clickable header).
            parts: list[str] = []
            for child in details.children:
                if getattr(child, "name", None) == "summary":
                    continue
                text = child.get_text(" ", strip=True) if hasattr(child, "get_text") else str(child).strip()
                if text:
                    parts.append(text)
            content = re.sub(r"\s+", " ", " ".join(parts)).strip()
            if content:
                out[label] = content
        return out

    @staticmethod
    def _first(d: dict[str, str], keys: tuple[str, ...]) -> str:
        for k in keys:
            v = d.get(k)
            if v:
                return v
        return ""

    @staticmethod
    def _extract_dimension(size_text: str, labels: tuple[str, ...]) -> str:
        """From 'רוחב: 36.0 ס״מ גובה: 73.0 ס״מ' extract '36.0 ס״מ' for 'רוחב'."""
        if not size_text:
            return ""
        for label in labels:
            m = re.search(rf"{label}\s*:?\s*([\d.,]+\s*ס[״\"\']?מ?)", size_text)
            if m:
                return m.group(1).strip()
        return ""

    # ── Strategy 2: HTML scraping fallback ───────────────────────

    def _scrape_via_html(self) -> list[SupplierProduct]:
        products: list[SupplierProduct] = []
        collection_urls = self._get_collection_urls()

        for col_url in collection_urls:
            links = self._get_product_links_from_collection(col_url)
            for url in links:
                try:
                    p = self._parse_html_product(url)
                    if p:
                        products.append(p)
                except Exception as exc:
                    self.logger.warning(f"HTML parse failed {url}: {exc}")

        return products

    def _get_collection_urls(self) -> list[str]:
        try:
            soup = self.http.get_soup(f"{BASE}/collections")
            links = []
            for a in soup.select("a[href^='/collections/']"):
                href = a.get("href", "")
                full = BASE + href.split("?")[0]
                if full not in links and not full.endswith("/collections"):
                    links.append(full)
            return links
        except Exception:
            return [f"{BASE}/collections/furniture"]

    def _get_product_links_from_collection(self, col_url: str) -> list[str]:
        links = []
        page = 1
        while True:
            url = f"{col_url}?page={page}"
            try:
                soup = self.http.get_soup(url)
                found = False
                for a in soup.select("a[href^='/products/']"):
                    href = BASE + a["href"].split("?")[0]
                    if href not in links:
                        links.append(href)
                        found = True
                if not found:
                    break
                page += 1
            except Exception:
                break
        return links

    def _parse_html_product(self, url: str) -> Optional[SupplierProduct]:
        soup = self.http.get_soup(url)
        name_el = soup.find("h1") or soup.find("h1", class_="product__title")
        name = name_el.get_text(strip=True) if name_el else ""
        if not name:
            return None

        price = find_price(soup)
        images = find_images(soup, BASE)
        desc_el = soup.find("div", class_="product__description") or soup.find(
            "div", class_="description"
        )
        desc = desc_el.get_text("\n", strip=True) if desc_el else ""

        return self._make_product(
            name=name,
            supplier_url=url,
            price=price,
            images=images,
            stock_status="instock",
            original_description=desc,
        )
