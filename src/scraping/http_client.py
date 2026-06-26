"""
Shared HTTP session with retry, timeout, and polite delay baked in.
All supplier scrapers should use this instead of raw requests.get.
"""
import time

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.core.constants import DEFAULT_TIMEOUT, DEFAULT_USER_AGENT
from src.core.logger import get_logger

# verify_ssl=False is intentional for this project (some supplier sites have
# old/self-signed certs). Silence urllib3's per-request warning so the console
# stays readable during a 2000-product scrape.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = get_logger(__name__)


def build_session(verify_ssl: bool = True) -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=5, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.verify = verify_ssl
    s.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    return s


class HttpClient:
    """Thin wrapper around requests.Session with logging and delay."""

    def __init__(self, verify_ssl: bool = True, request_delay: float = 1.0):
        self._session = build_session(verify_ssl)
        self._delay = request_delay

    def get(self, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        logger.debug(f"GET {url}")
        resp = self._session.get(url, **kwargs)
        resp.raise_for_status()
        time.sleep(self._delay)
        return resp

    def get_soup(self, url: str, **kwargs):
        from bs4 import BeautifulSoup
        resp = self.get(url, **kwargs)
        return BeautifulSoup(resp.text, "html.parser")

    def close(self) -> None:
        self._session.close()
