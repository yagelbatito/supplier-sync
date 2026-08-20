"""
Orchestrator: ties together everything for the WhatsApp → WC flow.

This module is the ONLY place that touches the existing project's services.
It mirrors the same enrichment + create/update flow that main.py uses for
the regular scrapers, just driven by WhatsApp events instead of a scrape().

Two public entry points called by the webhook server:
    handle_incoming_image(...)  — image message arrived
    handle_incoming_text(...)   — text message arrived (possibly a reply with price)
"""
import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.core.config_loader import SupplierConfig
from src.core.constants import STATUS_DRAFT, STOCK_IN, STOCK_OUT
from src.core.logger import get_logger
from src.core.utils import stable_sku
from src.enrichment.product_content_generator import ProductContentGenerator
from src.enrichment.prompts import FOOTER_NOTE
from src.matching.category_matcher import CategoryMatcher
from src.models.product import SupplierProduct
from src.whatsapp.message_parser import ParsedCommand, parse_command
from src.whatsapp.meta_client import WhatsAppClient
from src.whatsapp.ocr_service import OcrResult, OcrService
from src.whatsapp.pending_store import PendingStore
from src.woocommerce.category_service import CategoryService
from src.woocommerce.client import WooCommerceClient
from src.woocommerce.media_service import MediaService
from src.woocommerce.product_service import ProductService

logger = get_logger(__name__)


# Where we save WhatsApp images locally before uploading to WC.
# We keep them around so a re-sync (after the user fixes a price typo)
# doesn't need to re-download from Meta (whose URLs expire in 5 min).
IMAGE_DIR = "data/whatsapp_images"


class WhatsAppOrchestrator:
    """End-to-end coordinator for the WhatsApp → WC flow.

    Constructed once at startup by whatsapp_listener.build_orchestrator(),
    which wires it with the exact same service instances main.py uses.
    """

    def __init__(
        self,
        # WhatsApp-specific
        wa_client: WhatsAppClient,
        ocr: OcrService,
        store: PendingStore,
        # Existing-project services (same instances main.py uses)
        wc_client: WooCommerceClient,
        product_svc: ProductService,
        media_svc: MediaService,
        category_svc: CategoryService,
        category_matcher: CategoryMatcher,
        content_generator: ProductContentGenerator,
        suppliers_config: dict[str, SupplierConfig],
        shipping_class_map: Optional[dict[str, str]] = None,
    ):
        self.wa = wa_client
        self.ocr = ocr
        self.store = store
        self.wc_client = wc_client
        self.product_svc = product_svc
        self.media_svc = media_svc
        self.category_svc = category_svc
        self.category_matcher = category_matcher
        self.content_generator = content_generator
        self.suppliers_config = suppliers_config
        self.shipping_class_map = shipping_class_map or {}

        Path(IMAGE_DIR).mkdir(parents=True, exist_ok=True)

    # ── Entry: incoming image ────────────────────────────────────

    def handle_incoming_image(
        self,
        message_id: str,
        from_number: str,
        media_id: str,
        caption: str = "",
    ) -> None:
        """Process an inbound image message.

        Flow: download → OCR → save to pending store → reply with summary.
        If the caption already includes "ספק:X מחיר:Y", we run the full sync
        immediately without waiting for a separate reply.
        """
        logger.info(f"[WA] Image received: msg_id={message_id} from={from_number}")
        cmd = parse_command(caption) if caption else ParsedCommand()

        # 1. Download from Meta CDN (URLs expire in ~5min, need bearer auth)
        media = self.wa.download_media(media_id)
        if not media:
            self.wa.send_text(
                from_number,
                "❌ נכשלה הורדת התמונה מהשרת. נסה לשלוח שוב.",
                reply_to_msg_id=message_id,
            )
            return
        image_bytes, mime = media

        # Persist locally — extension based on mime.
        ext = ".jpg" if "jpeg" in mime else (".png" if "png" in mime else ".bin")
        local_path = os.path.join(IMAGE_DIR, f"{message_id}{ext}")
        try:
            with open(local_path, "wb") as f:
                f.write(image_bytes)
        except OSError as exc:
            logger.error(f"Could not save image to {local_path}: {exc}")

        # 2. OCR. We tell the user we're working so they don't think it's stuck.
        self.wa.send_text(from_number, "⏳ מעבד את התמונה...", reply_to_msg_id=message_id)
        ocr_result = self.ocr.extract(image_bytes, mime_type=mime)

        if not ocr_result.is_usable() and not cmd.has_manual_product_details():
            # Plain product photo with no readable text — that's fine (most
            # furniture photos have no text). Save it as a pending item and ask
            # the user to type the details. Their follow-up message (even as a
            # NEW message, not a formal reply) matches this via the
            # latest-pending fallback in handle_incoming_text.
            self.store.save(message_id, {
                "from": from_number,
                "image_local_path": local_path,
                "image_mime": mime,
                "ocr": ocr_result.raw_json,
                "received_at": datetime.now(timezone.utc).isoformat(),
                "status": "awaiting_price",
            })
            self.wa.send_text(
                from_number,
                "📷 קיבלתי את התמונה!\n"
                "עכשיו שלח את הפרטים (אפשר בהודעה נפרדת):\n"
                "שם:<שם המוצר>  מחיר:<מחיר>  קטגוריה:<קטגוריה>\n\n"
                "לדוגמה: שם:שולחן אלון נפתח  מחיר:4900  קטגוריה:פינות אוכל מעץ",
                reply_to_msg_id=message_id,
            )
            return

        self._apply_command_overrides(ocr_result, cmd)

        # 3. Save to pending store
        record = {
            "from": from_number,
            "image_local_path": local_path,
            "image_mime": mime,
            "ocr": ocr_result.raw_json,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "status": "awaiting_price",
        }
        self.store.save(message_id, record)

        # 4. If the caption already has the full command, run sync inline.
        # Otherwise, ask for the missing fields.
        if cmd.is_complete():
            self._run_sync(message_id, from_number, ocr_result, cmd, local_path, mime)
            return

        summary = ocr_result.display_summary() or "(לא חולץ סיכום)"
        missing = " + ".join(cmd.missing_fields()) if cmd.missing_fields() else "ספק + מחיר"
        self.wa.send_text(
            from_number,
            f"✅ זוהה:\n{summary}\n\n"
            f"השב להודעה זו עם:\n"
            f"שם:{ocr_result.name or '<שם המוצר>'} מידות:<מידות> מחיר:150 קטגוריה:<קטגוריה>\n\n"
            f"(חסר: {missing}. אפשר להוסיף גם: מקט:XXX, ספק:perlahome)",
            reply_to_msg_id=message_id,
        )

    # ── Entry: incoming text (a reply) ───────────────────────────

    def handle_incoming_text(
        self,
        message_id: str,
        from_number: str,
        body: str,
        reply_to_msg_id: Optional[str] = None,
    ) -> None:
        """Process an inbound text message.

        If it's a reply to a known pending image, parse + sync.
        Otherwise, try to use the most recent pending item as fallback.
        """
        logger.info(f"[WA] Text received: msg_id={message_id} reply_to={reply_to_msg_id} body={body!r}")

        cmd = parse_command(body)

        # Find which pending record this command refers to.
        target_msg_id: Optional[str] = None
        record: Optional[dict] = None

        if reply_to_msg_id:
            rec = self.store.get(reply_to_msg_id)
            if rec and rec.get("status") == "awaiting_price":
                target_msg_id = reply_to_msg_id
                record = rec
        if not record:
            # Fallback: latest pending item for this user.
            candidates = [
                (mid, rec) for mid, rec in self.store.list_pending()
                if rec.get("from") == from_number
            ]
            if candidates:
                candidates.sort(key=lambda x: x[1].get("received_at", ""), reverse=True)
                target_msg_id, record = candidates[0]
                logger.info(f"[WA] No reply context — using latest pending {target_msg_id}")

        if not record:
            # No image is waiting for a price → treat the text as a management
            # command (mark out of stock / delete) or a product-lookup query.
            self._handle_command_or_query(from_number, body, message_id)
            return

        # Cancel command
        if cmd.is_cancel:
            self.store.delete(target_msg_id)
            self.wa.send_text(from_number, "🗑️ המוצר נמחק מהממתינים.", reply_to_msg_id=message_id)
            return

        if not cmd.is_complete():
            missing = " + ".join(cmd.missing_fields())
            self.wa.send_text(
                from_number,
                f"⚠️ חסר: {missing}.\n"
                f"דוגמה: ספק:perlahome מחיר:150",
                reply_to_msg_id=message_id,
            )
            return

        # Reconstruct OcrResult from stored JSON.
        ocr_result = OcrResult(
            **{k: v for k, v in record.get("ocr", {}).items() if k in OcrResult.__dataclass_fields__ and k != "raw_json"},
            raw_json=record.get("ocr", {}),
        )

        # Apply user overrides from the reply text.
        self._apply_command_overrides(ocr_result, cmd)

        self._run_sync(
            target_msg_id,
            from_number,
            ocr_result,
            cmd,
            record.get("image_local_path", ""),
            record.get("image_mime", "image/jpeg"),
            reply_to_msg_id=message_id,
        )

    # ── Text commands & product lookup (no image pending) ────────

    def _search_products(self, query: str, limit: int = 5) -> list[dict]:
        if not query:
            return []
        try:
            return self.wc_client.get(
                "products", params={"search": query, "per_page": limit, "status": "any"}
            ) or []
        except Exception as exc:
            logger.error(f"[WA] product search failed: {exc}")
            return []

    # Owner-only: this handler is reached ONLY for owner numbers (the webhook
    # routes non-owners to the designer bot), so the menu is private by design.
    _MENU = (
        "🛠️ *תפריט ניהול*\n\n"
        "📷 *העלאת מוצר* — שלח תמונה + פרטים\n"
        "🔍 *בדיקת מחיר* — `<שם מוצר>`\n"
        "🔖 *מק\"ט* — `מקט <שם>`\n"
        "💰 *עדכון מחיר* — `מחיר <שם> <מחיר>`\n"
        "🚚 *עדכון משלוח* — `משלוח <שם> קטן/בינוני/גדול`\n"
        "📝 *הוספה לתיאור* — `הוסף תיאור <שם>, <טקסט>`\n"
        "🔄 *החלפת משפט בתיאור* — `החלף תיאור <שם>, <ישן>, <חדש>`\n"
        "📦 *סימון אזל* — `אזל <שם>`\n"
        "🗑️ *מחיקה* — `מחק <שם>`\n\n"
        "הקלד *תפריט* בכל רגע כדי לראות שוב את הרשימה."
    )

    def _send_menu(self, from_number: str, message_id: str) -> None:
        self.wa.send_text(from_number, self._MENU, reply_to_msg_id=message_id)

    def _handle_command_or_query(self, from_number: str, body: str, message_id: str) -> None:
        text = (body or "").strip()
        if not text:
            self._send_menu(from_number, message_id)
            return
        # show the command menu on request
        if text in ("תפריט", "עזרה", "פקודות", "menu", "Menu", "help", "?", "עזרא"):
            self._send_menu(from_number, message_id)
            return
        # management: description — append / replace a sentence (before others,
        # so "תיאור" text isn't swallowed by the product-lookup fallback)
        for kw in ("הוסף תיאור", "הוסף לתיאור", "תיאור+"):
            if text.startswith(kw):
                self._cmd_desc_append(from_number, text[len(kw):].strip(" :,-–\t"), message_id)
                return
        for kw in ("החלף תיאור", "החלף בתיאור", "תיאור~"):
            if text.startswith(kw):
                self._cmd_desc_replace(from_number, text[len(kw):].strip(" :,-–\t"), message_id)
                return
        # lookup: fetch a product's SKU — "מקט <שם>"
        for kw in ("מקט", "מק\"ט", "מק״ט", "מק'ט", "sku", "SKU"):
            if text.startswith(kw):
                self._cmd_sku(from_number, text[len(kw):].strip(" :,-–\t"), message_id)
                return
        # management: mark out of stock + draft
        for kw in ("אזל", "נגמר", "מכר", "מכרתי", "אין במלאי", "נמכר"):
            if text.startswith(kw):
                self._cmd_out_of_stock(from_number, text[len(kw):].strip(" :,-–\t"), message_id)
                return
        # management: delete (to trash — reversible)
        for kw in ("מחק", "תמחק", "הסר"):
            if text.startswith(kw):
                self._cmd_delete(from_number, text[len(kw):].strip(" :,-–\t"), message_id)
                return
        # management: shipping class — "משלוח <שם> קטן/בינוני/גדול"
        for kw in ("עדכן משלוח", "שנה משלוח", "משלוח"):
            if text.startswith(kw):
                self._cmd_shipping(from_number, text[len(kw):].strip(" :,-–\t"), message_id)
                return
        # management: set price — "עדכן מחיר <שם> <מחיר>" / "מחיר <שם> <מספר>"
        m = re.match(r"^(?:עדכן\s+מחיר|שנה\s+מחיר|מחיר)\s+(.+?)\s+([\d,]+(?:\.\d+)?)\s*₪?$", text)
        if m:
            self._cmd_set_price(from_number, m.group(1).strip(), m.group(2), message_id)
            return
        # otherwise: product lookup query — strip a leading question word
        q = text
        for kw in ("כמה עולה", "מה המחיר של", "מה המחיר", "חפש", "מחיר", "בדוק"):
            if q.startswith(kw):
                q = q[len(kw):]
                break
        q = q.strip(" :?,-–\t").rstrip("?").strip()
        self._query_products(from_number, q, message_id)

    def _query_products(self, from_number: str, query: str, message_id: str) -> None:
        results = self._search_products(query, limit=3)
        if not results:
            self.wa.send_text(from_number, f'🔍 לא נמצא מוצר בשם "{query}".', reply_to_msg_id=message_id)
            return
        self.wa.send_text(from_number, f'🔍 נמצאו {len(results)} תוצאות עבור "{query}":', reply_to_msg_id=message_id)
        for p in results:
            price = p.get("price") or p.get("regular_price") or "?"
            stock = "✅ במלאי" if p.get("stock_status") == "instock" else "❌ אזל מהמלאי"
            caption = f"🪑 {p.get('name','')}\n💰 {price} ₪  |  {stock}"
            if p.get("status") != "publish":
                caption += f"\n⚠️ סטטוס: {p.get('status')}"
            if p.get("permalink"):
                caption += f"\n🔗 {p['permalink']}"
            imgs = p.get("images") or []
            if imgs and imgs[0].get("src"):
                self.wa.send_image(from_number, imgs[0]["src"], caption)
            else:
                self.wa.send_text(from_number, caption)

    def _find_by_sku(self, sku: str) -> Optional[dict]:
        """Exact SKU lookup (lets the user target ONE product among same-named
        variants). Tries the value as-is and upper-cased (our SKUs are upper)."""
        sku = (sku or "").strip()
        if not sku:
            return None
        for candidate in {sku, sku.upper()}:
            try:
                res = self.wc_client.get("products", params={"sku": candidate})
            except Exception:
                res = None
            if res:
                return res[0]
        return None

    def _resolve_one(self, from_number: str, query: str, message_id: str, verb: str) -> Optional[dict]:
        """Pick a single product to act on, or message the user and return None.

        Accepts a NAME or an exact מק"ט (SKU). When several products share the
        name, we list them WITH their SKU so the user can re-run the command
        with the exact SKU to target one precisely.
        """
        if not query:
            self.wa.send_text(from_number, f"כתוב את שם המוצר {verb}. למשל: `אזל שולחן לורי`", reply_to_msg_id=message_id)
            return None
        # exact SKU wins — the precise way to pick among same-named products
        by_sku = self._find_by_sku(query)
        if by_sku:
            return by_sku
        results = self._search_products(query, limit=8)
        if not results:
            self.wa.send_text(from_number, f'🔍 לא נמצא מוצר בשם "{query}".', reply_to_msg_id=message_id)
            return None
        # exact (case-insensitive) name match wins — but only if it's unique
        exact = [p for p in results if p.get("name", "").strip() == query.strip()]
        if len(results) == 1:
            return results[0]
        if len(exact) == 1:
            return exact[0]
        lines = [f'נמצאו {len(results)} מוצרים ל"{query}". כדי לדייק — הרץ שוב את הפקודה עם המק"ט מהרשימה:']
        for p in results[:8]:
            price = p.get("price") or p.get("regular_price") or "?"
            sku = p.get("sku") or "—"
            lines.append(f'• {p.get("name","")} — {price} ₪  ·  מק"ט: {sku}')
        self.wa.send_text(from_number, "\n".join(lines), reply_to_msg_id=message_id)
        return None

    def _cmd_out_of_stock(self, from_number: str, query: str, message_id: str) -> None:
        p = self._resolve_one(from_number, query, message_id, "לסמן כאזל")
        if not p:
            return
        try:
            self.wc_client.put(f"products/{p['id']}", {"stock_status": "outofstock", "status": "draft"})
            self.wa.send_text(from_number, f'📦 "{p.get("name","")}" סומן כ*אזל מהמלאי* והורד לטיוטה.', reply_to_msg_id=message_id)
        except Exception as exc:
            logger.error(f"[WA] out-of-stock failed: {exc}")
            self.wa.send_text(from_number, "⚠️ נכשל עדכון המוצר. נסה שוב.", reply_to_msg_id=message_id)

    def _cmd_delete(self, from_number: str, query: str, message_id: str) -> None:
        p = self._resolve_one(from_number, query, message_id, "למחוק")
        if not p:
            return
        try:
            self.wc_client.delete(f"products/{p['id']}", params={"force": "false"})
            self.wa.send_text(from_number, f'🗑️ "{p.get("name","")}" הועבר לפח (ניתן לשחזור באתר).', reply_to_msg_id=message_id)
        except Exception as exc:
            logger.error(f"[WA] delete failed: {exc}")
            self.wa.send_text(from_number, "⚠️ נכשלה המחיקה. נסה שוב.", reply_to_msg_id=message_id)

    def _cmd_set_price(self, from_number: str, name: str, price_str: str, message_id: str) -> None:
        try:
            price = float(price_str.replace(",", ""))
        except ValueError:
            self.wa.send_text(from_number, "מחיר לא תקין.", reply_to_msg_id=message_id)
            return
        p = self._resolve_one(from_number, name, message_id, "לעדכן מחיר")
        if not p:
            return
        val = str(int(price)) if price == int(price) else str(price)
        try:
            self.wc_client.put(f"products/{p['id']}", {"regular_price": val})
            self.wa.send_text(from_number, f'💰 "{p.get("name","")}" → {val} ₪ ✅', reply_to_msg_id=message_id)
        except Exception as exc:
            logger.error(f"[WA] price update failed: {exc}")
            self.wa.send_text(from_number, "⚠️ עדכון המחיר נכשל. נסה שוב.", reply_to_msg_id=message_id)

    _SHIP = {"קטן": "משלוח-קטן", "בינוני": "משלוח-בינוני", "גדול": "משלוח-גדול"}

    def _cmd_shipping(self, from_number: str, args: str, message_id: str) -> None:
        parts = args.rsplit(None, 1)  # split off the last word as the size
        size = parts[-1] if parts else ""
        if len(parts) < 2 or size not in self._SHIP:
            self.wa.send_text(
                from_number,
                "שימוש: `משלוח <שם מוצר> קטן/בינוני/גדול`\nלמשל: `משלוח שולחן לורי גדול`",
                reply_to_msg_id=message_id)
            return
        name, slug = parts[0].strip(), self._SHIP[size]
        p = self._resolve_one(from_number, name, message_id, "לעדכן משלוח")
        if not p:
            return
        try:
            self.wc_client.put(f"products/{p['id']}", {"shipping_class": slug})
            self.wa.send_text(from_number, f'🚚 "{p.get("name","")}" → משלוח {size} ✅', reply_to_msg_id=message_id)
        except Exception as exc:
            logger.error(f"[WA] shipping update failed: {exc}")
            self.wa.send_text(from_number, "⚠️ עדכון המשלוח נכשל. נסה שוב.", reply_to_msg_id=message_id)

    def _cmd_sku(self, from_number: str, query: str, message_id: str) -> None:
        """`מקט <שם>` — reply with the product's SKU."""
        if not query:
            self.wa.send_text(from_number, "כתוב שם מוצר. למשל: `מקט שולחן לורי`", reply_to_msg_id=message_id)
            return
        p = self._resolve_one(from_number, query, message_id, "לשליפת מק\"ט")
        if not p:
            return
        sku = p.get("sku") or "(אין מק\"ט)"
        self.wa.send_text(
            from_number,
            f'🔖 "{p.get("name","")}"\nמק"ט: {sku}',
            reply_to_msg_id=message_id)

    @staticmethod
    def _desc_split(args: str, maxparts: int) -> list:
        """Split description args by comma OR pipe (pipe wins if present, so a
        text that itself contains commas can still be delimited with |)."""
        sep = "|" if "|" in args else ","
        return [x.strip() for x in args.split(sep, maxparts - 1)]

    def _cmd_desc_append(self, from_number: str, args: str, message_id: str) -> None:
        """`הוסף תיאור <שם>, <טקסט>` — append text to the product description."""
        parts = self._desc_split(args, 2)
        name = parts[0] if len(parts) >= 1 else ""
        addition = parts[1] if len(parts) >= 2 else ""
        if not name or not addition:
            self.wa.send_text(
                from_number,
                "שימוש: `הוסף תיאור <שם מוצר>, <הטקסט להוספה>`\n"
                "למשל: `הוסף תיאור שולחן לורי, משלוח חינם עד הבית`",
                reply_to_msg_id=message_id)
            return
        p = self._resolve_one(from_number, name, message_id, "לעדכן תיאור")
        if not p:
            return
        try:
            # Re-fetch by id so we edit the CURRENT full description (the search
            # result can be stale/truncated).
            full = self.wc_client.get(f"products/{p['id']}")
            cur = (full.get("description") or "")
            new_desc = (cur.rstrip() + f"\n<p>{addition}</p>") if cur.strip() else f"<p>{addition}</p>"
            self.wc_client.put(f"products/{p['id']}", {"description": new_desc})
            self.wa.send_text(
                from_number,
                f'📝 נוסף לתיאור של "{p.get("name","")}":\n"{addition}" ✅',
                reply_to_msg_id=message_id)
        except Exception as exc:
            logger.error(f"[WA] desc append failed: {exc}")
            self.wa.send_text(from_number, "⚠️ עדכון התיאור נכשל. נסה שוב.", reply_to_msg_id=message_id)

    def _cmd_desc_replace(self, from_number: str, args: str, message_id: str) -> None:
        """`החלף תיאור <שם>, <ישן>, <חדש>` — replace a phrase in the description."""
        parts = self._desc_split(args, 3)  # name, old, new (new may contain commas)
        if len(parts) < 3 or not parts[0] or not parts[1] or not parts[2]:
            self.wa.send_text(
                from_number,
                "שימוש: `החלף תיאור <שם מוצר>, <משפט ישן>, <משפט חדש>`\n"
                "למשל: `החלף תיאור שולחן לורי, אורך 200 ס\"מ, אורך 220 ס\"מ`",
                reply_to_msg_id=message_id)
            return
        name, old, new = parts[0], parts[1], parts[2]
        p = self._resolve_one(from_number, name, message_id, "לעדכן תיאור")
        if not p:
            return
        try:
            full = self.wc_client.get(f"products/{p['id']}")
            cur = (full.get("description") or "")
            if old not in cur:
                self.wa.send_text(
                    from_number,
                    f'⚠️ לא נמצא הטקסט "{old}" בתיאור של "{p.get("name","")}".\n'
                    f"ודא שהוא כתוב בדיוק כמו בתיאור.",
                    reply_to_msg_id=message_id)
                return
            new_desc = cur.replace(old, new, 1)
            self.wc_client.put(f"products/{p['id']}", {"description": new_desc})
            self.wa.send_text(
                from_number,
                f'📝 עודכן בתיאור של "{p.get("name","")}":\n"{old}" → "{new}" ✅',
                reply_to_msg_id=message_id)
        except Exception as exc:
            logger.error(f"[WA] desc replace failed: {exc}")
            self.wa.send_text(from_number, "⚠️ עדכון התיאור נכשל. נסה שוב.", reply_to_msg_id=message_id)

    # ── Core sync logic ──────────────────────────────────────────

    def _run_sync(
        self,
        pending_msg_id: str,
        from_number: str,
        ocr: OcrResult,
        cmd: ParsedCommand,
        image_local_path: str,
        image_mime: str,
        reply_to_msg_id: Optional[str] = None,
    ) -> None:
        """
        Build a SupplierProduct from OCR + user-supplied price, then run it
        through the same enrichment + WC create/update pipeline that main.py
        uses for scraping suppliers. Mirrors main._process_product().
        """
        reply_target = reply_to_msg_id or pending_msg_id

        # 1. Resolve supplier config
        supplier_key = cmd.supplier or "whatsapp"
        config = self._get_supplier_config(supplier_key)
        if not config:
            self.wa.send_text(
                from_number,
                f"❌ ספק לא ידוע: {supplier_key!r}\n"
                f"ספקים אפשריים: {', '.join(sorted(self.suppliers_config.keys()))}",
                reply_to_msg_id=reply_target,
            )
            return

        # 2. Build the SupplierProduct
        product = self._build_product(ocr, cmd, config, pending_msg_id)

        # 3. ── Category matching ──────────────────────────────
        # If the user explicitly typed a category, honour it — mapped to the
        # closest REAL WC category — instead of letting the keyword matcher
        # override it from the product name (which could point at a category
        # that doesn't exist on the site, dumping the product to manual review).
        user_cat = self._resolve_user_category(cmd.category_override)
        if user_cat:
            product.mapped_category = user_cat
            logger.info(f"[WA] Using user category '{cmd.category_override}' → '{user_cat}'")
        else:
            try:
                matched = self.category_matcher.match(
                    supplier_key=config.key,
                    supplier_category=product.supplier_category,
                    product_name=product.name,
                    product_description=product.original_description,
                    default_category=config.default_category,
                )
                # Never publish into a category that doesn't exist on the site —
                # snap it to the closest real one so we avoid "בדיקה ידנית".
                product.mapped_category = self._closest_real_category(
                    matched, default=config.default_category,
                )
            except Exception as exc:
                logger.error(f"Category matching failed: {exc}", exc_info=True)
                product.mapped_category = config.default_category

        # 4. ── Handle OOS (not really applicable to WhatsApp items, but
        #       preserves the existing field invariants) ─────────────────
        if not product.is_available and config.draft_when_out_of_stock:
            product.status = STATUS_DRAFT
            product.stock_status = STOCK_OUT

        # 5. ── OpenAI enrichment ─────────────────────────────────
        # Mirrors main._create_new() — call only when generator is available.
        if self.content_generator.available:
            try:
                product = self.content_generator.enrich(product)
            except Exception as exc:
                logger.error(f"Enrichment failed: {exc}", exc_info=True)
                self.wa.send_text(
                    from_number,
                    f"⚠️ שלב ההעשרה (OpenAI) נכשל: {exc}\n"
                    f"המוצר לא הועלה.",
                    reply_to_msg_id=reply_target,
                )
                return
        else:
            logger.warning("[WA] ProductContentGenerator unavailable — skipping enrichment")

        # 5b. ── Guarantee the EXACT, full dimensions in the description ──
        # The AI paraphrases the prose and sometimes drops a dimension
        # (e.g. length/אורך). Append the user's dimensions verbatim as an
        # explicit spec line (inserted before the contact footer), so the
        # full measurements are always present and correct.
        dims_text = (getattr(ocr, "dimensions_text", "") or "").strip()
        if dims_text:
            spec_line = f"📐 מידות: {dims_text}"
            fd = product.full_description or ""
            if dims_text not in fd:
                if FOOTER_NOTE and FOOTER_NOTE in fd:
                    fd = fd.replace(FOOTER_NOTE, spec_line + "\n\n" + FOOTER_NOTE, 1)
                else:
                    fd = (fd.rstrip() + "\n\n" + spec_line) if fd else spec_line
                product.full_description = fd

        # 6. ── Upload image (WhatsApp image is local bytes, NOT a URL) ──
        # Same pattern main.py uses for paldinox: pre-upload via wc_client
        # and pass attachment IDs to the product create call.
        attachment_id = self._upload_local_image(image_local_path, image_mime)
        images_payload = [{"id": attachment_id}] if attachment_id else []
        if not images_payload:
            product.mark_for_review("No image uploaded from WhatsApp")

        # 7. ── Resolve categories + shipping class ──────────────
        category_ids = self.category_svc.resolve_list(product.mapped_category, config.default_category)
        shipping_class = self.shipping_class_map.get(product.mapped_category, "")

        # 8. ── Find existing → update or create ─────────────────
        try:
            existing = self.product_svc.find_by_sku(product.sku)
            if existing:
                wc_id = existing["id"]
                if not images_payload:
                    images_payload = self._existing_images_payload(existing)
                self.product_svc.update(
                    wc_id, product, category_ids, images_payload,
                    shipping_class=shipping_class,
                )
                action = "updated"
            else:
                result = self.product_svc.create(
                    product, category_ids, images_payload,
                    shipping_class=shipping_class,
                )
                wc_id = result.get("id")
                action = "created"
        except Exception as exc:
            logger.error(f"WC upsert failed: {exc}", exc_info=True)
            self.wa.send_text(
                from_number,
                f"❌ העלאה ל-WooCommerce נכשלה: {exc}",
                reply_to_msg_id=reply_target,
            )
            self.store.mark_status(pending_msg_id, "error", {"error": str(exc)})
            return

        # 9. Confirm back in WhatsApp
        emoji = "✅" if action == "created" else "🔄"
        review_note = ""
        if getattr(product, "requires_manual_review", False):
            reason = getattr(product, "manual_review_reason", "")
            review_note = f"\n⚠️ מצריך review: {reason}" if reason else "\n⚠️ מצריך review"

        self.wa.send_text(
            from_number,
            f"{emoji} {action} — WC ID: {wc_id}\n"
            f"מק\"ט: {product.sku}\n"
            f"שם: {product.display_name() if hasattr(product, 'display_name') else product.name}\n"
            f"מחיר: {product.calculated_price:.2f} ₪\n"
            f"קטגוריה: {product.mapped_category}"
            f"{review_note}",
            reply_to_msg_id=reply_target,
        )
        self.store.mark_status(pending_msg_id, "synced", {"wc_id": wc_id, "action": action})

    # ── Helpers ──────────────────────────────────────────────────

    def _get_supplier_config(self, supplier_key: Optional[str]) -> Optional[SupplierConfig]:
        if not supplier_key:
            return None
        if supplier_key in self.suppliers_config:
            return self.suppliers_config[supplier_key]
        # Case-insensitive fallback.
        lower = supplier_key.lower()
        for k, cfg in self.suppliers_config.items():
            if k.lower() == lower:
                return cfg
        return None

    def _closest_real_category(self, name: Optional[str], default: str = "") -> str:
        """Snap a proposed category name to the closest REAL WC category.

        Returns the name unchanged if it already exists on the site; otherwise
        the closest existing category (fuzzy) so we never publish into a
        non-existent category. Falls back to `default` if nothing is close.
        """
        if not name:
            return default
        name = name.strip()
        reals = self.category_svc.category_names
        if not reals or name in reals:
            return name
        from thefuzz import process as _fuzz
        result = _fuzz.extractOne(name, reals)
        if result and result[1] >= 75:
            logger.info(f"[WA] Category '{name}' not on site → closest '{result[0]}' (score={result[1]})")
            return result[0]
        return default

    def _resolve_user_category(self, name: Optional[str]) -> Optional[str]:
        """If the user typed a category, return the matching REAL WC category
        (exact or close fuzzy). Returns None when nothing is close enough, so
        the caller falls back to the keyword matcher."""
        if not name or not name.strip():
            return None
        resolved = self._closest_real_category(name, default="")
        return resolved or None

    def _build_product(
        self,
        ocr: OcrResult,
        cmd: ParsedCommand,
        cfg: SupplierConfig,
        pending_msg_id: str,
    ) -> SupplierProduct:
        """Build a SupplierProduct from OCR + user input, ready for the pipeline."""
        # SKU: prefer user override → OCR'd SKU → stable hash of message_id.
        # NOTE: we must HASH the wamid, not use it raw. Every wamid from the
        # same sender starts with the same ~34 chars (they encode the phone
        # number), and stable_sku truncates the id to 30 — so raw wamids
        # collapsed to ONE SKU and each new product overwrote the previous one.
        # A short sha1 of the full wamid keeps every product unique.
        if cmd.sku_override:
            sku_input = cmd.sku_override
        elif ocr.sku:
            sku_input = ocr.sku
        else:
            sku_input = "wa-" + hashlib.sha1(pending_msg_id.encode("utf-8")).hexdigest()[:12]
        sku = stable_sku(
            supplier_key=cfg.key,
            product_id=sku_input,
            product_url="",  # no canonical URL for WhatsApp items
            prefix=cfg.sku_prefix,
        )

        # The user's price is the FINAL selling price.
        # Suppliers in suppliers.json have price_multiplier like 0.7 (perlahome),
        # which the BaseSupplier.run() applies via calculate_price(). But this
        # WhatsApp flow does NOT go through run() — we build the product
        # ourselves, so the multiplier is naturally bypassed. We set both
        # price and calculated_price to the user's input.
        final_price = cmd.price or 0.0

        # Description: stitch together the OCR-extracted bits so the
        # enrichment step has rich raw text to work from.
        desc_parts: list[str] = []
        if ocr.raw_description:
            desc_parts.append(ocr.raw_description)
        if ocr.brand:
            desc_parts.append(f"מותג: {ocr.brand}")
        if ocr.dimensions_text and not any([ocr.width, ocr.height, ocr.depth]):
            desc_parts.append(f"מידות: {ocr.dimensions_text}")
        original_description = "\n".join(desc_parts).strip()

        product = SupplierProduct(
            supplier_name=cfg.supplier_name,
            supplier_key=cfg.key,
            supplier_product_id=sku_input,
            supplier_url="",  # no source URL for WhatsApp products
            sku=sku,
            name=ocr.name,
            original_description=original_description,
            price=final_price,
            stock_status=STOCK_IN,
            is_available=True,
            supplier_category=cmd.category_override or ocr.category_hint or cfg.default_category,
            images=[],  # we upload local bytes, not via URL
            color=ocr.color,
            material=ocr.material,
            width=ocr.width,
            height=ocr.height,
            depth=ocr.depth,
            dimensions=ocr.dimensions_text,
        )
        # Bypass the standard multiplier — the user gave us the FINAL price.
        product.calculated_price = final_price

        # Low-confidence OCR → manual review flag, so we don't auto-publish
        # something the model hallucinated.
        if ocr.confidence == "low" and not cmd.has_manual_product_details() and hasattr(product, "mark_for_review"):
            product.mark_for_review("OCR confidence low — verify name/SKU")
        return product

    @staticmethod
    def _apply_command_overrides(ocr: OcrResult, cmd: ParsedCommand) -> None:
        if cmd.name_override:
            ocr.name = cmd.name_override
            ocr.raw_json["name"] = cmd.name_override
        if cmd.sku_override:
            ocr.sku = cmd.sku_override
            ocr.raw_json["sku"] = cmd.sku_override
        if cmd.category_override:
            ocr.category_hint = cmd.category_override
            ocr.raw_json["category_hint"] = cmd.category_override
        if cmd.dimensions_override:
            ocr.dimensions_text = cmd.dimensions_override
            ocr.raw_json["dimensions_text"] = cmd.dimensions_override
        if cmd.width_override:
            ocr.width = cmd.width_override
            ocr.raw_json["width"] = cmd.width_override
        if cmd.height_override:
            ocr.height = cmd.height_override
            ocr.raw_json["height"] = cmd.height_override
        if cmd.depth_override:
            ocr.depth = cmd.depth_override
            ocr.raw_json["depth"] = cmd.depth_override

    def _upload_local_image(self, local_path: str, mime: str) -> Optional[int]:
        """Upload a locally-stored image (WhatsApp media) to WC media library.

        Same mechanism main.py uses for paldinox — go through MediaService's
        WC client to register the image as a media attachment, then attach
        by ID rather than URL.
        """
        if not local_path or not os.path.exists(local_path):
            logger.warning(f"No local image to upload: {local_path}")
            return None
        try:
            with open(local_path, "rb") as f:
                content = f.read()
        except OSError as exc:
            logger.error(f"Read image failed: {exc}")
            return None

        filename = os.path.basename(local_path) or "whatsapp.jpg"
        try:
            return self.wc_client.upload_media(content, filename, mime)
        except Exception as exc:
            logger.error(f"WC media upload failed: {exc}")
            return None

    @staticmethod
    def _existing_images_payload(existing: dict) -> list[dict]:
        """Keep current WC product images when a replacement image is unavailable."""
        payload: list[dict] = []
        for image in existing.get("images", []) or []:
            image_id = image.get("id")
            if image_id:
                payload.append({"id": image_id})
        return payload
