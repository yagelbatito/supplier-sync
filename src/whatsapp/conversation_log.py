"""
Lightweight append-only log of customer↔bot conversations.

WhatsApp Cloud API messages never touch a phone app — they arrive at our
webhook — so the shop owner has no native inbox. We record every customer
message and every bot reply here (JSONL) so the /conversations page can show
them. One line per message; reads take the tail so the file can grow.

Note: on Render's free tier the disk is wiped on each redeploy, so history
resets when we deploy a new version. Good enough for "see recent chats"; a
durable DB would be the upgrade if long-term history is needed.
"""
import json
import os
from datetime import datetime, timezone
from threading import Lock

from src.core.logger import get_logger

logger = get_logger(__name__)

LOG_PATH = os.getenv("CONVERSATION_LOG_PATH", "data/conversations.jsonl")
_lock = Lock()


def log_message(phone: str, role: str, text: str) -> None:
    """Append one message. role: 'user' (customer) | 'bot' | 'rec' (recommendation)."""
    text = (text or "").strip()
    if not phone or not text:
        return
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "phone": phone,
        "role": role,
        "text": text,
    }
    try:
        os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
        with _lock, open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning(f"[conv-log] write failed: {exc}")


def read_all(max_lines: int = 8000) -> list[dict]:
    """Return the last `max_lines` logged messages (oldest→newest)."""
    if not os.path.exists(LOG_PATH):
        return []
    try:
        with _lock, open(LOG_PATH, encoding="utf-8") as f:
            lines = f.readlines()[-max_lines:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out
