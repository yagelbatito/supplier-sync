"""WooCommerce REST API client."""
import base64

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.core.exceptions import WooCommerceError
from src.core.logger import get_logger

logger = get_logger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

class WooCommerceClient:
    def __init__(self, url, consumer_key, consumer_secret,
                 wp_user, wp_app_password, verify_ssl=True, dry_run=False):
        self.base_url = url.rstrip("/")
        self.api_base = f"{self.base_url}/wp-json/wc/v3"
        self.wp_base = f"{self.base_url}/wp-json/wp/v2"
        self._key = consumer_key
        self._secret = consumer_secret
        self._wp_user = wp_user
        self._wp_password = wp_app_password
        self._verify = verify_ssl
        self.dry_run = dry_run
        retry = Retry(total=3, backoff_factor=0.5,
                      status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE"]))
        self._session = requests.Session()
        self._session.mount("https://", HTTPAdapter(max_retries=retry))
        self._session.mount("http://", HTTPAdapter(max_retries=retry))
        self._session.headers.update({"User-Agent": UA})

    def _p(self, extra=None):
        p = {"consumer_key": self._key, "consumer_secret": self._secret}
        if extra:
            p.update(extra)
        return p

    def get(self, endpoint, params=None):
        url = f"{self.api_base}/{endpoint.lstrip('/')}"
        resp = self._session.get(url, params=self._p(params), verify=self._verify, timeout=30)
        self._check(resp, "GET", url)
        return resp.json()

    def get_all(self, endpoint, per_page=100, **params):
        results, page = [], 1
        while True:
            batch = self.get(endpoint, params={"per_page": per_page, "page": page, **params})
            if not batch:
                break
            results.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return results

    def post(self, endpoint, data):
        if self.dry_run:
            logger.info(f"[DRY-RUN] POST {endpoint}: {list(data.keys())}")
            return {"id": 0, "sku": data.get("sku", ""), "name": data.get("name", "")}
        url = f"{self.api_base}/{endpoint.lstrip('/')}"
        resp = self._session.post(url, params=self._p(), json=data, verify=self._verify, timeout=60)
        self._check(resp, "POST", url)
        return resp.json()

    def put(self, endpoint, data):
        if self.dry_run:
            logger.info(f"[DRY-RUN] PUT {endpoint}: {list(data.keys())}")
            return {"id": 0}
        url = f"{self.api_base}/{endpoint.lstrip('/')}"
        resp = self._session.put(url, params=self._p(), json=data, verify=self._verify, timeout=60)
        self._check(resp, "PUT", url)
        return resp.json()

    def wp_auth_headers(self):
        if not self._wp_user or not self._wp_password:
            return {}
        token = base64.b64encode(
            f"{self._wp_user}:{self._wp_password}".encode()).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    def upload_media(self, image_data, filename, mime_type="image/jpeg"):
        if self.dry_run:
            logger.info(f"[DRY-RUN] Would upload: {filename}")
            return None
        if not self._wp_user or not self._wp_password:
            logger.warning("WP credentials missing")
            return None
        url = f"{self.wp_base}/media"
        headers = {**self.wp_auth_headers(),
                   "Content-Disposition": f'attachment; filename="{filename}"',
                   "Content-Type": mime_type}
        resp = self._session.post(url, headers=headers, data=image_data,
                                  verify=self._verify, timeout=60)
        if resp.status_code not in (200, 201):
            logger.error(f"Media upload failed ({resp.status_code}): {filename}")
            return None
        return resp.json().get("id")

    def _check(self, resp, method, url):
        if resp.status_code not in (200, 201):
            raise WooCommerceError(
                f"{method} {url} -> {resp.status_code}: {resp.text[:300]}")
