"""
WordPress media upload service.
Handles image downloading, deduplication, and uploading.
Supports authenticated image downloads (e.g. Paldinox B2B).
"""
import hashlib
import mimetypes
import os
import re
from typing import Optional

import requests

from src.core.logger import get_logger
from src.woocommerce.client import WooCommerceClient

logger = get_logger(__name__)

VALID_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_SIZE_MB = 10


class MediaService:
    def __init__(self, client: WooCommerceClient, verify_ssl: bool = True):
        self._client = client
        self._verify = verify_ssl
        self._uploaded_hashes: set[str] = set()
        # Extra auth headers per domain (e.g. for Paldinox)
        self._domain_auth: dict[str, dict] = {}

    def register_domain_auth(self, domain: str, headers: dict) -> None:
        """Register auth headers for a specific domain."""
        self._domain_auth[domain] = headers

    def upload_product_images(self, image_urls: list[str], max_images: int = 5) -> list[int]:
        attachment_ids = []
        for url in image_urls[:max_images]:
            try:
                att_id = self._upload_one(url)
                if att_id:
                    attachment_ids.append(att_id)
            except Exception as exc:
                logger.warning(f"Image upload failed ({url}): {exc}")
        return attachment_ids

    def _upload_one(self, url: str) -> Optional[int]:
        if not self._is_valid_image_url(url):
            logger.debug(f"Skipping invalid image URL: {url}")
            return None

        # Download image — with domain-specific auth if needed
        try:
            headers = self._get_auth_headers(url)
            resp = requests.get(url, timeout=20, verify=self._verify, headers=headers)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning(f"Could not download image {url}: {exc}")
            return None

        content = resp.content
        if not content:
            return None

        size_mb = len(content) / (1024 * 1024)
        if size_mb > MAX_IMAGE_SIZE_MB:
            logger.warning(f"Image too large ({size_mb:.1f}MB): {url}")
            return None

        content_hash = hashlib.md5(content).hexdigest()
        if content_hash in self._uploaded_hashes:
            logger.debug(f"Image already uploaded: {url}")
            return None

        mime = resp.headers.get("Content-Type", "").split(";")[0].strip()
        if not mime or mime not in VALID_MIME_TYPES:
            mime = self._guess_mime(url)
        if not mime:
            logger.warning(f"Unknown image type: {url}")
            return None

        filename = self._filename_from_url(url, mime)
        att_id = self._client.upload_media(content, filename, mime)
        if att_id:
            self._uploaded_hashes.add(content_hash)
            logger.debug(f"Uploaded image ID={att_id}")

        return att_id

    def _get_auth_headers(self, url: str) -> dict:
        """Return auth headers if this domain requires them."""
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        for domain, auth_headers in self._domain_auth.items():
            if domain in url:
                headers.update(auth_headers)
                break

        # Auto-detect Paldinox from env
        if "paldinox" in url:
            token = os.getenv("PALDINOX_TOKEN", "").strip()
            if token:
                headers["Cookie"] = f"token={token}"
                headers["Referer"] = "https://b2b.paldinox.co.il/AmPortal/catalog"

        return headers

    def _is_valid_image_url(self, url: str) -> bool:
        if not url or not url.startswith(("http://", "https://")):
            return False
        skip = ["logo", "icon", "banner", "placeholder", "sprite", ".svg", "pixel"]
        return not any(s in url.lower() for s in skip)

    def _guess_mime(self, url: str) -> Optional[str]:
        lower = url.lower().split("?")[0]
        if lower.endswith((".jpg", ".jpeg")):
            return "image/jpeg"
        if lower.endswith(".png"):
            return "image/png"
        if lower.endswith(".webp"):
            return "image/webp"
        if lower.endswith(".gif"):
            return "image/gif"
        return None

    def _filename_from_url(self, url: str, mime: str) -> str:
        ext = mimetypes.guess_extension(mime) or ".jpg"
        path = url.split("?")[0].rstrip("/")
        basename = path.split("/")[-1]
        basename = re.sub(r"[^\w\-.]", "-", basename)
        if not any(basename.lower().endswith(e) for e in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
            basename += ext
        return basename[:100]
