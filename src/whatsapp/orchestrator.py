"""
Orchestrator: ties together everything for the WhatsApp → WC flow.

This module is the ONLY place that touches the existing project's services.
It mirrors the same enrichment + create/update flow that main.py uses for
the regular scrapers, just driven by WhatsApp events instead of a scrape().

Two public entry points called by the webhook server:
    handle_incoming_image(...)  — image message arrived
    handle_incoming_text(...)   — text message arrived (possibly a reply with price)
"""
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.core.config_loader import SupplierConfig
from src.core.constants import STATUS_DRAFT, STOCK_IN, STOCK_OUT
from src.core.logger import get_logger
from src.core.utils import stable_sku
from src.enrichment.product_content_generator import ProductContentGenerator
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
            self.wa.send_text(
                from_number,
                "⚠️ לא הצלחתי לזהות פרטי מוצר בתמונה.\n"
                "שלח שוב, או הוסף ידנית בתגובה:\n"
                "שם:<שם המוצר> מידות:<מידות> מחיר:<מחיר> קטגוריה:<קטגוריה>",
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
            self.wa.send_text(
                from_number,
                "🤔 אין מוצר ממתין למחיר. שלח תחילה תמונה של המוצר.",
                reply_to_msg_id=message_id,
            )
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
        # Mirrors main.py:_process_product step "Category matching"
        try:
            product.mapped_category = self.category_matcher.match(
                supplier_key=config.key,
                supplier_category=product.supplier_category,
                product_name=product.name,
                product_description=product.original_description,
                default_category=config.default_category,
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

    def _build_product(
        self,
        ocr: OcrResult,
        cmd: ParsedCommand,
        cfg: SupplierConfig,
        pending_msg_id: str,
    ) -> SupplierProduct:
        """Build a SupplierProduct from OCR + user input, ready for the pipeline."""
        # SKU: prefer user override → OCR'd SKU → stable hash of message_id.
        # Using pending_msg_id as the stable key means resending the same image
        # WON'T create a duplicate; sending a new image of the same product
        # WILL get a new SKU (and find_by_sku won't match — caught at create).
        sku_input = cmd.sku_override or ocr.sku or f"wa-{pending_msg_id}"
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
