"""
FastAPI server that receives webhooks from Meta WhatsApp Cloud API.

Two routes:
    GET  /webhook/whatsapp  — verification handshake (Meta calls this once
                              when you register the webhook in their dashboard)
    POST /webhook/whatsapp  — message events (calls back here every time
                              a user sends a WhatsApp message to your number)

Security:
1. The GET handshake checks `hub.verify_token` against WHATSAPP_VERIFY_TOKEN.
2. POST requests are validated with X-Hub-Signature-256 against WHATSAPP_APP_SECRET.
3. Messages from numbers not in WHATSAPP_ALLOWED_NUMBERS are silently dropped.
"""
import hashlib
import hmac
import os
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from src.core.logger import get_logger
from src.whatsapp.orchestrator import WhatsAppOrchestrator

logger = get_logger(__name__)


def _allowed_numbers() -> set[str]:
    raw = os.getenv("WHATSAPP_ALLOWED_NUMBERS", "").strip()
    if not raw:
        return set()
    return {n.strip() for n in raw.split(",") if n.strip()}


def _verify_signature(body: bytes, signature_header: Optional[str]) -> bool:
    """Validate X-Hub-Signature-256 against WHATSAPP_APP_SECRET.

    If no app secret is configured we skip the check (dev mode), but log a
    warning. In production WHATSAPP_APP_SECRET must be set.
    """
    secret = os.getenv("WHATSAPP_APP_SECRET", "").strip()
    if not secret:
        logger.warning("WHATSAPP_APP_SECRET not set — skipping signature verification (dev mode only!)")
        return True
    if not signature_header:
        return False
    # Header format: "sha256=HEX"
    if not signature_header.startswith("sha256="):
        return False
    expected = signature_header.split("=", 1)[1]
    computed = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, computed)


def create_app(orchestrator: WhatsAppOrchestrator) -> FastAPI:
    """Create the FastAPI app, binding it to the given orchestrator instance.

    Factory pattern so callers (`whatsapp_listener.py`) can wire all
    dependencies before the app starts serving traffic.
    """
    app = FastAPI(title="WhatsApp → WooCommerce sync")
    allowed = _allowed_numbers()

    @app.get("/")
    def health():
        """Health check for cloud hosts (Render/Railway) — always 200."""
        return {"status": "ok", "service": "whatsapp-woocommerce"}

    @app.get("/webhook/whatsapp", response_class=PlainTextResponse)
    def verify(
        hub_mode: str = Query("", alias="hub.mode"),
        hub_challenge: str = Query("", alias="hub.challenge"),
        hub_verify_token: str = Query("", alias="hub.verify_token"),
    ):
        """Meta's one-time verification handshake."""
        expected_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()
        if hub_mode == "subscribe" and hub_verify_token == expected_token:
            logger.info("Webhook verification successful")
            return PlainTextResponse(hub_challenge)
        logger.warning(f"Webhook verification failed (mode={hub_mode!r})")
        raise HTTPException(status_code=403, detail="Verification failed")

    @app.post("/webhook/whatsapp")
    async def receive(
        request: Request,
        background: BackgroundTasks,
        x_hub_signature_256: Optional[str] = Header(None),
    ):
        body_bytes = await request.body()

        # 1. Signature check (optional in dev, required in prod)
        if not _verify_signature(body_bytes, x_hub_signature_256):
            logger.warning("Webhook signature validation failed")
            raise HTTPException(status_code=403, detail="Invalid signature")

        # 2. Parse JSON
        try:
            payload = await request.json()
        except Exception as exc:
            logger.error(f"Webhook JSON parse failed: {exc}")
            # Always 200 to Meta, otherwise they retry aggressively.
            return {"ok": False, "reason": "bad-json"}

        # 3. Walk the standard WhatsApp Cloud API payload structure.
        # Meta sends batched events; in practice for a single message there's
        # one entry → one change → one messages[0]. We loop to be safe.
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", []) or []
                for msg in messages:
                    # Offload to a background task so we ACK Meta quickly.
                    # Meta requires <20s response or it retries. Our OCR
                    # call alone can be 3-5s, so we MUST background it.
                    background.add_task(_dispatch, orchestrator, msg, allowed)

        return {"ok": True}

    return app


def _dispatch(orchestrator: WhatsAppOrchestrator, msg: dict, allowed: set[str]) -> None:
    """Route a single message to the orchestrator based on its type.

    Runs in BackgroundTasks (separate from the HTTP response cycle). Any
    exception here is fully caught and logged — we never let an unhandled
    error from one message poison the worker.
    """
    try:
        from_number = msg.get("from", "")
        msg_id = msg.get("id", "")
        msg_type = msg.get("type", "")

        # ── Allowlist gate ──────────────────────────────────────
        if allowed and from_number not in allowed:
            logger.warning(f"Ignored message from non-allowed number: {from_number}")
            return

        # Context: if this is a reply, we'll get context.id pointing to the
        # original message_id. Used to match the price reply to the right
        # pending image.
        context = msg.get("context") or {}
        reply_to = context.get("id")

        if msg_type == "image":
            image = msg.get("image", {}) or {}
            media_id = image.get("id", "")
            caption = image.get("caption", "") or ""
            if not media_id:
                logger.warning(f"Image message without media id: {msg_id}")
                return
            orchestrator.handle_incoming_image(
                message_id=msg_id,
                from_number=from_number,
                media_id=media_id,
                caption=caption,
            )

        elif msg_type == "text":
            text = msg.get("text", {}) or {}
            body = text.get("body", "") or ""
            orchestrator.handle_incoming_text(
                message_id=msg_id,
                from_number=from_number,
                body=body,
                reply_to_msg_id=reply_to,
            )

        else:
            # video / audio / document / etc — we don't process these yet.
            # Reply once to let the user know it was ignored.
            logger.info(f"Ignored message type: {msg_type} from {from_number}")
            orchestrator.wa.send_text(
                from_number,
                f"⚠️ סוג הודעה לא נתמך: {msg_type}. שלח תמונה של המוצר.",
                reply_to_msg_id=msg_id,
            )

    except Exception as exc:
        logger.error(f"Dispatch failed: {exc}", exc_info=True)
