# -*- coding: utf-8 -*-
"""
Durable, live-editable settings for the customer designer bot (סמדר AI).

WHY THIS EXISTS
    The bot's personality (system prompt), payment details and category skip
    list used to be hard-coded constants — changing them meant editing code and
    redeploying (Render's free disk is wiped on every deploy, so a plain JSON
    file wouldn't survive either). This module keeps those settings in a single
    PRIVATE WordPress page (base64-encoded JSON in its content), which:
      * survives redeploys (lives in the WP database), and
      * needs no extra infra — we already hold WP app-password credentials.

    The bot reads settings through `get(key)`; the /bot admin panel edits them
    with `save()` and applies them instantly with `reload()` — no redeploy.

DEFAULTS
    designer_bot.py registers the canonical defaults via `set_defaults()` at
    import time (so the big prompt string lives in exactly one place). `get()`
    returns a saved value when present, else the registered default.
"""
import base64
import json
import re
import threading

from src.core.logger import get_logger

logger = get_logger(__name__)

# Private WP page that stores the JSON. Title is a hint not to delete it.
_SLUG = "smadar-bot-settings"
_TITLE = "Smadar Bot Settings — do not delete"

# Editable keys the admin panel exposes (anything else is ignored on save).
EDITABLE_KEYS = ("system_prompt", "pay_link", "bit_phone", "bank_details",
                 "deposit_pct", "skip_categories")

_DEFAULTS: dict = {}
_state = {"data": None, "page_id": None}      # data = saved overrides (dict)
_lock = threading.Lock()


def set_defaults(d: dict) -> None:
    """Called once by designer_bot with the canonical fallback values."""
    _DEFAULTS.update(d)


def get(key):
    """Saved value if present, else the registered default."""
    data = _state["data"]
    if data is not None and key in data and data[key] not in (None, ""):
        return data[key]
    return _DEFAULTS.get(key)


def current() -> dict:
    """Defaults overlaid with saved overrides — what the panel shows/edits."""
    merged = dict(_DEFAULTS)
    if _state["data"]:
        for k, v in _state["data"].items():
            if v not in (None, ""):
                merged[k] = v
    return merged


# ── WordPress page persistence ────────────────────────────────────
def _wp_get(wc, path, params=None):
    url = f"{wc.wp_base}/{path.lstrip('/')}"
    r = wc._session.get(url, headers=wc.wp_auth_headers(), params=params,
                        verify=wc._verify, timeout=30)
    return r


def _wp_post(wc, path, payload):
    url = f"{wc.wp_base}/{path.lstrip('/')}"
    r = wc._session.post(url, headers=wc.wp_auth_headers(), json=payload,
                         verify=wc._verify, timeout=30)
    return r


def _find_page_id(wc):
    r = _wp_get(wc, "pages", {"slug": _SLUG, "status": "any", "context": "edit"})
    if r.status_code == 200:
        arr = r.json()
        if arr:
            return arr[0]["id"]
    return None


def _decode(raw_content: str) -> dict:
    """Pull the base64 JSON back out of the (possibly HTML-wrapped) content."""
    if not raw_content:
        return {}
    b64 = re.sub(r"[^A-Za-z0-9+/=]", "", raw_content)      # drop <p>, <br>, ws
    if not b64:
        return {}
    pad = (-len(b64)) % 4
    b64 += "=" * pad
    try:
        return json.loads(base64.b64decode(b64).decode("utf-8"))
    except Exception as exc:
        logger.warning(f"bot_settings: could not decode stored content: {exc}")
        return {}


def _encode(data: dict) -> str:
    return base64.b64encode(json.dumps(data, ensure_ascii=False).encode("utf-8")).decode("ascii")


def load(wc, force: bool = False) -> dict:
    """Read saved overrides from WP into memory. Best-effort: on any failure we
    keep whatever we had (or defaults). Returns the merged settings."""
    with _lock:
        if _state["data"] is not None and not force:
            return current()
        try:
            pid = _state["page_id"] or _find_page_id(wc)
            if pid:
                _state["page_id"] = pid
                r = _wp_get(wc, f"pages/{pid}", {"context": "edit"})
                if r.status_code == 200:
                    raw = (r.json().get("content", {}) or {}).get("raw", "")
                    _state["data"] = _decode(raw)
                    logger.info(f"bot_settings: loaded {len(_state['data'])} override(s) from WP page {pid}")
                else:
                    _state["data"] = _state["data"] or {}
            else:
                _state["data"] = _state["data"] or {}     # nothing saved yet
                logger.info("bot_settings: no settings page yet — using defaults")
        except Exception as exc:
            logger.warning(f"bot_settings: load failed ({exc}); using defaults")
            _state["data"] = _state["data"] or {}
    return current()


def save(wc, updates: dict) -> dict:
    """Merge `updates` (only EDITABLE_KEYS) into saved overrides and persist to
    WP. Applies immediately in-process. Returns the merged settings."""
    clean = {}
    for k in EDITABLE_KEYS:
        if k not in updates:
            continue
        v = updates[k]
        if k == "deposit_pct":
            try:
                v = float(v)
                if not (0 < v <= 1):
                    v = _DEFAULTS.get("deposit_pct", 0.5)
            except (TypeError, ValueError):
                continue
        if k == "skip_categories" and isinstance(v, str):
            v = [s.strip() for s in v.splitlines() if s.strip()]
        clean[k] = v

    with _lock:
        data = dict(_state["data"] or {})
        data.update(clean)
        body = _encode(data)
        pid = _state["page_id"] or _find_page_id(wc)
        payload = {"content": body, "status": "private", "title": _TITLE}
        if pid:
            r = _wp_post(wc, f"pages/{pid}", payload)
        else:
            payload["slug"] = _SLUG
            r = _wp_post(wc, "pages", payload)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"WP save failed {r.status_code}: {r.text[:200]}")
        _state["page_id"] = r.json().get("id", pid)
        _state["data"] = data
        logger.info(f"bot_settings: saved {list(clean.keys())} to WP page {_state['page_id']}")
    return current()
