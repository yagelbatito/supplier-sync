# -*- coding: utf-8 -*-
"""Abandoned-payment recovery webhook for smadarbetitohome.co.il.

When a checkout is abandoned the site cancels the order after ~1h and POSTs the
customer's details + a ready message here. We send the customer a WhatsApp
recovery message (an approved Utility *template*, since the customer has not
messaged us first), honour quiet hours / Shabbat, de-dupe per order, and forward
any customer reply to the owner.

Routes (added to the bot's FastAPI app):
    POST /webhooks/smadar-recovery        — the site calls this (header auth)
    POST /webhooks/smadar-recovery/flush  — sends queued (quiet-hour) messages;
                                            called by a cron and opportunistically

Env:
    SMADAR_REC_SECRET         shared secret for the X-Smadar-Secret header
    SMADAR_REC_TEMPLATE       approved WhatsApp template NAME (Utility category)
    SMADAR_REC_TEMPLATE_LANG  template language code (default "he")
    WHATSAPP_OWNER_NUMBERS / WHATSAPP_ALLOWED_NUMBERS  owner phone (for test/forward)
"""
import json
import os
import threading
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Asia/Jerusalem")
except Exception:  # pragma: no cover
    _TZ = None

from fastapi import Header, Request
from fastapi.responses import JSONResponse

from src.core.logger import get_logger

logger = get_logger(__name__)

# Default secret from the site's integration doc; override with SMADAR_REC_SECRET.
_DEFAULT_SECRET = "03432c3b19918c7e40e37f8f0a70c99b"
STATE_PATH = os.path.join("data", "recovery_state.json")
_LOCK = threading.Lock()
_OPT_OUT_WORDS = ("הסר", "להסיר", "הסירו", "תסיר", "לא מעוניין", "לא מעונין", "stop", "unsubscribe")
RECIPIENT_TTL_DAYS = 14


def _secret() -> str:
    return (os.getenv("SMADAR_REC_SECRET") or _DEFAULT_SECRET).strip()


def _owner_number() -> str:
    raw = (os.getenv("WHATSAPP_OWNER_NUMBERS", "") or os.getenv("WHATSAPP_ALLOWED_NUMBERS", "")).strip()
    return raw.split(",")[0].strip() if raw else ""


def _now_il() -> datetime:
    return datetime.now(_TZ) if _TZ else datetime.now()


# ── tiny JSON state store (ephemeral on free tier — best effort) ─────
def _load() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"sent": {}, "queue": [], "recipients": {}, "optout": []}


def _save(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.error(f"[recovery] state save failed: {exc}")


# ── quiet-hours / Shabbat → when may we send? ────────────────────────
def _send_after(now: datetime):
    """Return None if OK to send now, else a datetime to defer until.

    Quiet hours 21:00–09:00 → next 09:00. Shabbat (approx: Fri 15:00 → Sat 20:30)
    → Sat 20:30. The Shabbat window is intentionally conservative; it can be made
    exact later with a zmanim API."""
    wd, hour, minute = now.weekday(), now.hour, now.minute  # Mon=0 … Sun=6; Fri=4 Sat=5
    # Shabbat
    if wd == 4 and hour >= 15:  # Friday afternoon onward
        return now.replace(hour=20, minute=30, second=0, microsecond=0) + timedelta(days=1)
    if wd == 5 and (hour < 20 or (hour == 20 and minute < 30)):  # Saturday before motzei
        return now.replace(hour=20, minute=30, second=0, microsecond=0)
    # Nightly quiet hours 21:00–09:00
    if hour >= 21:
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    if hour < 9:
        return now.replace(hour=9, minute=0, second=0, microsecond=0)
    return None


def _do_send(orchestrator, rec: dict) -> bool:
    """Send one recovery message to the customer. Returns True on success."""
    wa = orchestrator.wa
    to = str(rec.get("phone_intl") or "").strip()
    if not to:
        return False
    tpl = os.getenv("SMADAR_REC_TEMPLATE", "").strip()
    if tpl:
        lang = os.getenv("SMADAR_REC_TEMPLATE_LANG", "he").strip() or "he"
        mid = wa.send_template(to, tpl, lang, [rec.get("first_name") or "", rec.get("recover_url") or ""])
    else:
        # No template configured → raw text (only lands if we're inside a 24h
        # window with this customer; cold sends will be rejected by Meta).
        mid = wa.send_text(to, rec.get("message") or "")
    return bool(mid)


def _flush(orchestrator) -> int:
    """Send any queued messages whose defer time has passed. Returns count sent."""
    now = _now_il()
    sent_n = 0
    with _LOCK:
        state = _load()
        keep = []
        for rec in state.get("queue", []):
            try:
                due = datetime.fromisoformat(rec.get("send_after"))
            except Exception:
                due = now
            if due <= now:
                if _do_send(orchestrator, rec):
                    oid = str(rec.get("order_id"))
                    state["sent"][oid] = now.isoformat()
                    if rec.get("phone_intl"):
                        state["recipients"][str(rec["phone_intl"])] = {
                            "order_id": oid, "sent_at": now.isoformat()}
                    sent_n += 1
                else:
                    keep.append(rec)  # retry next flush
            else:
                keep.append(rec)
        state["queue"] = keep
        _save(state)
    if sent_n:
        logger.info(f"[recovery] flushed {sent_n} queued message(s)")
    return sent_n


def handle_customer_reply(orchestrator, from_number: str, text: str, msg_id: str) -> bool:
    """If `from_number` was sent a recovery message, forward their reply to the
    owner (and handle opt-out). Returns True if handled (caller must NOT also run
    the designer bot on it)."""
    with _LOCK:
        state = _load()
        rec = state.get("recipients", {}).get(str(from_number))
        if not rec:
            return False
        # expire old recipients so we don't forward forever
        try:
            if datetime.fromisoformat(rec["sent_at"]) < datetime.now(_TZ) - timedelta(days=RECIPIENT_TTL_DAYS):
                state["recipients"].pop(str(from_number), None)
                _save(state)
                return False
        except Exception:
            pass
        order_id = rec.get("order_id", "?")
        low = (text or "").strip().lower()
        opted = any(w in low for w in _OPT_OUT_WORDS)
        if opted:
            state.setdefault("optout", [])
            if from_number not in state["optout"]:
                state["optout"].append(from_number)
            state["recipients"].pop(str(from_number), None)
        _save(state)

    owner = _owner_number()
    if opted:
        orchestrator.wa.send_text(from_number, "סומנת ולא תקבל/י מאיתנו תזכורות נוספות. תודה 🙏", reply_to_msg_id=msg_id)
        if owner:
            orchestrator.wa.send_text(owner, f"🚫 לקוח ביקש להסיר מתזכורות (הזמנה #{order_id}): {from_number}")
        return True
    # forward the reply to the owner; do NOT auto-answer price/shipping questions
    if owner:
        orchestrator.wa.send_text(owner, f"💬 תשובת לקוח לתזכורת (הזמנה #{order_id}, {from_number}):\n{text}")
    return True


def register_recovery_tool(app, orchestrator) -> None:

    @app.post("/webhooks/smadar-recovery")
    async def smadar_recovery(request: Request, x_smadar_secret: str = Header(None)):
        if not x_smadar_secret or x_smadar_secret.strip() != _secret():
            return JSONResponse(status_code=401, content={"ok": False, "error": "unauthorized"})
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "bad-json"})

        order_id = str(body.get("order_id") or "").strip()
        if not order_id:
            return JSONResponse(status_code=400, content={"ok": False, "error": "no order_id"})

        # opportunistic flush of anything now due
        try:
            _flush(orchestrator)
        except Exception as exc:
            logger.warning(f"[recovery] flush on POST failed: {exc}")

        is_test = bool(body.get("test"))

        # ── test: send to OWNER only, never the customer ──
        if is_test:
            owner = _owner_number()
            if not owner:
                return JSONResponse(status_code=500, content={"ok": False, "error": "no owner number"})
            msg = body.get("message") or ""
            ok = orchestrator.wa.send_text(owner, f"בדיקה – הזמנה #{order_id}\n\n{msg}")
            return JSONResponse(status_code=200 if ok else 500,
                                content={"ok": bool(ok), "test": True})

        with _LOCK:
            state = _load()
            # de-dupe: one message per order, ever
            if order_id in state.get("sent", {}):
                return JSONResponse(status_code=200, content={"ok": True, "deduped": True})
            # opted-out customer: never send
            if str(body.get("phone_intl")) in state.get("optout", []):
                return JSONResponse(status_code=200, content={"ok": True, "opted_out": True})
            defer = _send_after(_now_il())
            if defer is not None:
                rec = {k: body.get(k) for k in ("order_id", "first_name", "phone_intl",
                                                "recover_url", "message")}
                rec["send_after"] = defer.isoformat()
                state.setdefault("queue", []).append(rec)
                _save(state)
                return JSONResponse(status_code=200, content={"ok": True, "queued_until": defer.isoformat()})

        # send now
        ok = _do_send(orchestrator, body)
        if not ok:
            return JSONResponse(status_code=500, content={"ok": False, "error": "send failed"})
        with _LOCK:
            state = _load()
            state.setdefault("sent", {})[order_id] = _now_il().isoformat()
            if body.get("phone_intl"):
                state.setdefault("recipients", {})[str(body["phone_intl"])] = {
                    "order_id": order_id, "sent_at": _now_il().isoformat()}
            _save(state)
        return JSONResponse(status_code=200, content={"ok": True, "sent": True})

    @app.post("/webhooks/smadar-recovery/flush")
    def smadar_recovery_flush(x_smadar_secret: str = Header(None)):
        if not x_smadar_secret or x_smadar_secret.strip() != _secret():
            return JSONResponse(status_code=401, content={"ok": False})
        n = _flush(orchestrator)
        return {"ok": True, "sent": n}
