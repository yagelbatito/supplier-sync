"""
Paldinox supplier scraper.
Site: https://b2b.paldinox.co.il
Uses the internal REST API directly.
Supports automatic login — no manual token copy needed.
"""
import json
import os
from typing import Optional

from src.core.config_loader import SupplierConfig
from src.models.product import SupplierProduct
from src.scraping.http_client import HttpClient
from src.suppliers.base_supplier import BaseSupplier

BASE = "https://b2b.paldinox.co.il"
LOGIN_URL = f"{BASE}/AmPortal/api/Auth/login"
API_URL = f"{BASE}/AmPortal/api/Sales/getProductsForClient"
IMAGE_API = f"{BASE}/AmPortal/api/Attachments/images"

STOCK_MAP = {1: "instock", 2: "outofstock"}

DEVICE_INFO = json.dumps({
    "os": {"name": "Android", "version": "6.0"},
    "browser": {"name": "Chrome", "version": "148.0"},
    "device": {"type": "mobile", "platform": "Win32", "memory": 32, "cpuCores": 24,
                "isTouch": True, "isStandalone": False},
    "screen": {"width": 360, "height": 738, "pixelRatio": 2.0},
    "network": {"isOnline": True},
    "language": "he-IL",
    "timeZone": "Asia/Jerusalem",
    "userAgent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36"
})


class PaldinoxSupplier(BaseSupplier):
    def __init__(self, config: SupplierConfig, http: HttpClient):
        super().__init__(config, http)
        self._username = os.getenv("PALDINOX_USER", "").strip()
        self._password = os.getenv("PALDINOX_PASSWORD", "").strip()
        self._doc_num = os.getenv("PALDINOX_DOC_NUM", "").strip()
        self._token = ""

    def _login(self) -> bool:
        """Login and get fresh token automatically."""
        if not self._username or not self._password:
            self.logger.error(
                "PALDINOX_USER and PALDINOX_PASSWORD not set in .env"
            )
            return False

        self.logger.info("Logging in to Paldinox...")
        try:
            resp = self.http._session.post(
                LOGIN_URL,
                json={
                    "userName": self._username,
                    "password": self._password,
                    "forceLogin": False,
                    "deviceAppVer": "1.1.35",
                    "deviceInfo": DEVICE_INFO,
                    "language": "he",
                },
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
                    "Origin": BASE,
                    "Referer": f"{BASE}/AmPortal/",
                },
                verify=False,
                timeout=30,
            )
            resp.raise_for_status()

            # Extract token from response cookies or body
            token = resp.cookies.get("token", "")
            if not token:
                # Try response body
                data = resp.json()
                token = data.get("token", "") or data.get("data", {}).get("token", "")

            if not token:
                # Try Set-Cookie header
                set_cookie = resp.headers.get("Set-Cookie", "")
                for part in set_cookie.split(";"):
                    part = part.strip()
                    if part.startswith("token="):
                        token = part[6:]
                        break

            if token:
                self._token = token
                # Expose the fresh token so MediaService can download the
                # token-protected product images (it reads PALDINOX_TOKEN).
                os.environ["PALDINOX_TOKEN"] = token
                self.logger.info(f"✅ Paldinox login successful — token: {token[:20]}...")
                return True
            else:
                self.logger.error(f"Login response had no token: {resp.text[:300]}")
                return False

        except Exception as exc:
            self.logger.error(f"Login failed: {exc}")
            return False

    def scrape(self) -> list[SupplierProduct]:
        # Try auto-login first
        if not self._login():
            # Fallback to manual token from .env
            self._token = os.getenv("PALDINOX_TOKEN", "").strip()
            if not self._token:
                self.logger.error("No token available — set PALDINOX_USER+PALDINOX_PASSWORD or PALDINOX_TOKEN in .env")
                return []
            self.logger.info("Using manual PALDINOX_TOKEN from .env")

        products: list[SupplierProduct] = []
        page = 1
        page_size = 40

        while True:
            batch = self._fetch_page(page, page_size)
            if not batch:
                break

            self.logger.info(f"  Page {page}: {len(batch)} products")

            for raw in batch:
                try:
                    p = self._parse_product(raw)
                    if p:
                        products.append(p)
                except Exception as exc:
                    self.logger.warning(f"  Parse failed {raw.get('productID')}: {exc}")

            if len(batch) < page_size:
                break
            page += 1

        self.logger.info(f"Total scraped: {len(products)}")
        return products

    def _fetch_page(self, page: int, page_size: int) -> list[dict]:
        payload = {
            "amodatDocTypeID": 13,
            "departmentIDs": [],
            "docNum": self._doc_num,
            "isDefaultsApplied": True,
            "page": page,
            "pageSize": page_size,
            "productID": "",
            "productName": "",
            "showOnlyClientProducts": False,
            "showOnlyDocProducts": False,
            "showOnlyFavoritesProducts": False,
            "sortColumn": "",
            "sortOrder": "",
            "userParams": {
                "webViewSettings": {
                    "screens": [{"screenID": "HomeB2BClient", "screenMode": "*"}]
                }
            }
        }

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "Cookie": f"token={self._token}",
            "Origin": BASE,
            "Referer": f"{BASE}/AmPortal/catalog",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        try:
            resp = self.http._session.post(
                API_URL,
                json=payload,
                headers=headers,
                verify=False,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("status"):
                self.logger.error(f"API error: {data.get('message')}")
                return []

            self.logger.info(
                f"API response: status={data.get('status')} message={data.get('message')} items={len(data.get('data', []))}")

            return data.get("data", [])

        except Exception as exc:
            self.logger.error(f"API fetch failed page {page}: {exc}")
            return []

    def _get_image_url(self, image_name: str) -> str:
        return f"{IMAGE_API}/{image_name}?isThumbnail=false"

    def _parse_product(self, raw: dict) -> Optional[SupplierProduct]:
        product_id = raw.get("productID", "")
        name = raw.get("productName", "").strip()

        if not name or not product_id:
            return None

        price = float(raw.get("price", 0) or 0)
        stock_code = raw.get("stockStatus", 1)
        stock = STOCK_MAP.get(stock_code, "instock")
        category = raw.get("departmentName", "")

        image_name = raw.get("imageName", "")
        images = [self._get_image_url(image_name)] if image_name else []

        extra = self._parse_extra_fields(raw.get("extraFieldsInfo", ""))
        color = extra.get("צבע עיקרי", "")
        material = extra.get("חומר עיקרי", "")
        height = extra.get("גובה", "")
        width = extra.get("רוחב", "")
        depth = extra.get("אורך", "")
        brand = extra.get("מותג", "")

        desc = raw.get("productDescription", "") or ""
        if brand:
            desc = f"מותג: {brand}\n{desc}".strip()

        supplier_url = f"{BASE}/AmPortal/catalog?productID={product_id}"

        return self._make_product(
            name=name,
            supplier_url=supplier_url,
            price=price,
            images=images,
            stock_status=stock,
            supplier_product_id=product_id,
            supplier_category=category,
            original_description=desc,
            color=color,
            material=material,
            height=height,
            width=width,
            depth=depth,
        )

    def _parse_extra_fields(self, extra_json: str) -> dict:
        result = {}
        if not extra_json:
            return result
        try:
            fields = json.loads(extra_json)
            for f in fields:
                label = f.get("label", "").strip()
                value = f.get("value", "").strip()
                if label and value:
                    result[label] = value
        except Exception:
            pass
        return result
