"""
JSON-backed persistence for products that have been OCR'd but are still
waiting on the user to send a price.

Stored as `data/pending_whatsapp.json`:
{
  "<message_id_of_image>": {
    "from": "972501234567",
    "image_local_path": "data/whatsapp_images/wamid.abc.jpg",
    "image_mime": "image/jpeg",
    "ocr": { ... OcrResult.raw_json ... },
    "received_at": "2026-05-19T10:23:18+00:00",
    "status": "awaiting_price"  | "synced" | "error"
  },
  ...
}

We key by the WhatsApp message_id of the image (wamid.XXXX) because that's
what comes back as `context.id` when the user replies to the image.

Thread safety: a single file with a process-wide lock. Good enough — only
one webhook process should be running, and webhook handlers are inherently
serialized inside FastAPI's event loop for our handler.
"""
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.core.logger import get_logger

logger = get_logger(__name__)


_DEFAULT_PATH = "data/pending_whatsapp.json"
_DEFAULT_TTL_HOURS = 72


class PendingStore:
    def __init__(self, path: Optional[str] = None, ttl_hours: Optional[int] = None):
        self.path = path or os.getenv("WHATSAPP_PENDING_FILE", _DEFAULT_PATH)
        try:
            self.ttl_hours = int(os.getenv("WHATSAPP_PENDING_TTL_HOURS", str(ttl_hours or _DEFAULT_TTL_HOURS)))
        except ValueError:
            self.ttl_hours = _DEFAULT_TTL_HOURS
        self._lock = threading.Lock()

        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        if not os.path.exists(self.path):
            self._write({})

    # ── Read / Write primitives ──────────────────────────────────

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict) -> None:
        # Atomic write: tmp file → rename. Prevents corruption if the
        # process is killed mid-write.
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)

    # ── Public API ──────────────────────────────────────────────

    def save(self, message_id: str, record: dict) -> None:
        """Save a pending product, keyed by the image's WhatsApp message_id."""
        with self._lock:
            data = self._read()
            # Stamp received_at if the caller didn't.
            record.setdefault("received_at", datetime.now(timezone.utc).isoformat())
            record.setdefault("status", "awaiting_price")
            data[message_id] = record
            self._write(data)
        logger.info(f"Pending saved: {message_id} (status={record.get('status')})")

    def get(self, message_id: str) -> Optional[dict]:
        with self._lock:
            return self._read().get(message_id)

    def mark_status(self, message_id: str, status: str, extra: Optional[dict] = None) -> None:
        with self._lock:
            data = self._read()
            if message_id not in data:
                logger.warning(f"mark_status: {message_id} not found")
                return
            data[message_id]["status"] = status
            if extra:
                data[message_id].update(extra)
            self._write(data)

    def delete(self, message_id: str) -> None:
        with self._lock:
            data = self._read()
            if data.pop(message_id, None) is not None:
                self._write(data)

    def cleanup_expired(self) -> int:
        """Remove records older than TTL. Returns count removed."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.ttl_hours)
        removed = 0
        with self._lock:
            data = self._read()
            keys_to_remove = []
            for key, rec in data.items():
                try:
                    received = datetime.fromisoformat(rec.get("received_at", ""))
                except ValueError:
                    continue
                if received < cutoff:
                    keys_to_remove.append(key)
            for k in keys_to_remove:
                data.pop(k, None)
                removed += 1
            if removed:
                self._write(data)
        if removed:
            logger.info(f"Pending cleanup: removed {removed} expired records")
        return removed

    def list_pending(self) -> list[tuple[str, dict]]:
        """List all records currently in awaiting_price status."""
        with self._lock:
            return [
                (mid, rec) for mid, rec in self._read().items()
                if rec.get("status") == "awaiting_price"
            ]
