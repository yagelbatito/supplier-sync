"""
Thin client for WhatsApp Cloud API (Meta Graph API).

Three operations we actually need:
1. send_text() — reply back to the user in WhatsApp
2. get_media_url() — resolve a media_id to a temporary download URL
3. download_media() — fetch the actual bytes (requires the auth token, since
   Meta's media CDN is access-controlled even though URLs look public)
"""
import os
from typing import Optional

import httpx

from src.core.logger import get_logger

logger = get_logger(__name__)


class WhatsAppClient:
    """Synchronous WhatsApp Cloud API client.

    We use httpx (not requests) because elsewhere we may want to share a
    client with the FastAPI async server. The methods here are sync for
    simplicity — the webhook handler runs them in a threadpool.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        phone_number_id: Optional[str] = None,
        api_version: Optional[str] = None,
    ):
        self.token = (token or os.getenv("WHATSAPP_TOKEN", "")).strip()
        self.phone_number_id = (phone_number_id or os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")).strip()
        self.api_version = (api_version or os.getenv("WHATSAPP_API_VERSION", "v21.0")).strip()

        if not self.token:
            raise RuntimeError("WHATSAPP_TOKEN not configured")
        if not self.phone_number_id:
            raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID not configured")

        self._base = f"https://graph.facebook.com/{self.api_version}"
        self._client = httpx.Client(
            timeout=30.0,
            headers={"Authorization": f"Bearer {self.token}"},
        )

    # ── Sending ──────────────────────────────────────────────────

    def send_text(self, to: str, body: str, reply_to_msg_id: Optional[str] = None) -> Optional[str]:
        """Send a text message. Returns the sent message_id on success."""
        url = f"{self._base}/{self.phone_number_id}/messages"
        payload: dict = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": body, "preview_url": False},
        }
        if reply_to_msg_id:
            # Thread the reply onto the original message — the user sees it
            # as a proper reply with the quoted bubble. This makes the convo
            # readable when 5 different products are being processed.
            payload["context"] = {"message_id": reply_to_msg_id}

        try:
            resp = self._client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            messages = data.get("messages", [])
            if messages:
                return messages[0].get("id")
        except Exception as exc:
            logger.error(f"send_text failed (to={to}): {exc}")
        return None

    def send_image(self, to: str, image_url: str, caption: str = "") -> Optional[str]:
        """Send an image by public URL with an optional caption."""
        url = f"{self._base}/{self.phone_number_id}/messages"
        payload: dict = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "image",
            "image": {"link": image_url, "caption": caption[:1024]},
        }
        try:
            resp = self._client.post(url, json=payload)
            resp.raise_for_status()
            messages = resp.json().get("messages", [])
            if messages:
                return messages[0].get("id")
        except Exception as exc:
            logger.error(f"send_image failed (to={to}): {exc}")
            # fall back to a text message so the user still gets the info
            return self.send_text(to, caption)
        return None

    # ── Media download ───────────────────────────────────────────

    def get_media_url(self, media_id: str) -> Optional[str]:
        """Resolve a media_id to a temporary CDN URL.

        These URLs expire after ~5 minutes and require the auth token to
        actually download (the URL itself looks like a public S3 link but
        will 401 without the Bearer header).
        """
        try:
            resp = self._client.get(f"{self._base}/{media_id}")
            resp.raise_for_status()
            return resp.json().get("url")
        except Exception as exc:
            logger.error(f"get_media_url failed (media_id={media_id}): {exc}")
            return None

    def download_media(self, media_id: str) -> Optional[tuple[bytes, str]]:
        """Download media bytes by media_id.

        Returns (content_bytes, mime_type) or None on failure.
        """
        url = self.get_media_url(media_id)
        if not url:
            return None
        try:
            # Important: include Authorization on the CDN request too.
            resp = self._client.get(url)
            resp.raise_for_status()
            mime = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            return resp.content, mime
        except Exception as exc:
            logger.error(f"download_media failed (media_id={media_id}): {exc}")
            return None

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
