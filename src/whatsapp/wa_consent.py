# -*- coding: utf-8 -*-
"""
WhatsApp marketing consent (opt-in / opt-out) store.

Israeli spam law: marketing may go ONLY to people who opted in, every message
must offer a free opt-out, and opt-outs must be honored immediately. This module
keeps a durable per-phone consent record so the weekly WhatsApp campaign can send
only to opted-in, non-suppressed numbers.

Storage: a private WordPress page (base64 JSON), like bot_settings — survives
Render redeploys, uses the existing WP credentials.

    status(phone) -> "in" | "out" | None
    opt_in(wc, phone) / opt_out(wc, phone)
    opted_in_phones(wc) -> [phone, ...]
"""
import base64
import json
import re
import threading

from src.core.logger import get_logger

logger = get_logger(__name__)

_SLUG = "smadar-wa-consent"
_TITLE = "Smadar WhatsApp Consent — do not delete"
_state = {"data": None, "page_id": None}
_lock = threading.Lock()

# Free-text triggers a customer can send.
_OPT_OUT = re.compile(r"^\s*(הסר|הסרה|הסירו|הפסק|הפסיקו|ביטול|תסיר|stop|unsubscribe|remove)\s*$", re.IGNORECASE)
_OPT_IN = re.compile(r"(רוצה מבצעים|מבצעים שבועיים|הצטרפות למבצעים|כן מבצעים|לצרף אותי|מעוניין במבצעים|start)", re.IGNORECASE)

# Standard opt-out line to append to every marketing message (legal requirement).
OPT_OUT_LINE = "להסרה מרשימת הדיוור השב: הסר"


def is_opt_out(text: str) -> bool:
    return bool(_OPT_OUT.search(text or ""))


def is_opt_in(text: str) -> bool:
    return bool(_OPT_IN.search(text or ""))


def _norm(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    if d.startswith("972"):
        d = "0" + d[3:]
    return d


# ── WP page persistence (mirrors bot_settings) ──
def _wp_get(wc, path, params=None):
    url = f"{wc.wp_base}/{path.lstrip('/')}"
    return wc._session.get(url, headers=wc.wp_auth_headers(), params=params, verify=wc._verify, timeout=30)


def _wp_post(wc, path, payload):
    url = f"{wc.wp_base}/{path.lstrip('/')}"
    return wc._session.post(url, headers=wc.wp_auth_headers(), json=payload, verify=wc._verify, timeout=30)


def _find_page_id(wc):
    r = _wp_get(wc, "pages", {"slug": _SLUG, "status": "any", "context": "edit"})
    if r.status_code == 200 and r.json():
        return r.json()[0]["id"]
    return None


def _decode(raw):
    b64 = re.sub(r"[^A-Za-z0-9+/=]", "", raw or "")
    if not b64:
        return {}
    b64 += "=" * ((-len(b64)) % 4)
    try:
        return json.loads(base64.b64decode(b64).decode("utf-8"))
    except Exception:
        return {}


def load(wc, force=False):
    with _lock:
        if _state["data"] is not None and not force:
            return _state["data"]
        try:
            pid = _state["page_id"] or _find_page_id(wc)
            if pid:
                _state["page_id"] = pid
                r = _wp_get(wc, f"pages/{pid}", {"context": "edit"})
                _state["data"] = _decode((r.json().get("content", {}) or {}).get("raw", "")) if r.status_code == 200 else {}
            else:
                _state["data"] = {}
        except Exception as exc:
            logger.warning(f"wa_consent load failed: {exc}")
            _state["data"] = _state["data"] or {}
    return _state["data"]


def _save(wc):
    body = base64.b64encode(json.dumps(_state["data"], ensure_ascii=False).encode()).decode("ascii")
    payload = {"content": body, "status": "private", "title": _TITLE}
    pid = _state["page_id"] or _find_page_id(wc)
    r = _wp_post(wc, f"pages/{pid}" if pid else "pages", {**payload, **({} if pid else {"slug": _SLUG})})
    if r.status_code in (200, 201):
        _state["page_id"] = r.json().get("id", pid)
    else:
        logger.warning(f"wa_consent save failed: {r.status_code} {r.text[:120]}")


def status(phone):
    data = _state["data"] or {}
    rec = data.get(_norm(phone))
    return rec.get("s") if rec else None


def _set(wc, phone, s):
    import time
    load(wc)
    with _lock:
        _state["data"][_norm(phone)] = {"s": s, "ts": int(time.time())}
    _save(wc)


def opt_in(wc, phone):
    _set(wc, phone, "in")


def opt_out(wc, phone):
    _set(wc, phone, "out")


def opted_in_phones(wc):
    load(wc, force=True)
    return [p for p, rec in (_state["data"] or {}).items() if rec.get("s") == "in"]
