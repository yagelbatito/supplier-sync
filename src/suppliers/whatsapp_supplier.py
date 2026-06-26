"""
BaseSupplier-compatible adapter for WhatsApp.

You DON'T need this for normal operation — the webhook handles everything
in real-time. This adapter exists for one edge case:

    "I want to re-run the sync for everything that came through WhatsApp
     in the last 24h, as a batch, through main.py's normal flow."

It reads the pending_whatsapp.json store, finds records in `awaiting_price`
status that already have a price stored (e.g. set manually), and yields
SupplierProducts that main.py can iterate.

In day-to-day operation, the webhook + orchestrator do everything. This file
is here for completeness so the WhatsApp pipeline plugs into the existing
"sync all suppliers" mental model if you ever want it to.
"""
from src.core.config_loader import SupplierConfig
from src.models.product import SupplierProduct
from src.scraping.http_client import HttpClient
from src.suppliers.base_supplier import BaseSupplier
from src.whatsapp.pending_store import PendingStore


class WhatsAppSupplier(BaseSupplier):
    """A 'supplier' whose source is the WhatsApp pending store, not a website.

    Note: This class deliberately marks scrapes as "incomplete" by default,
    because the pending store only contains items the user happened to send —
    it's not a comprehensive catalog. We don't want main.py to draft real
    products just because they weren't in WhatsApp this week.
    """

    def __init__(self, config: SupplierConfig, http: HttpClient):
        super().__init__(config, http)
        self.store = PendingStore()
        # Always mark incomplete — the WhatsApp store isn't a catalog.
        self.mark_scrape_incomplete("WhatsApp inbox is not a catalog source")

    def scrape(self) -> list[SupplierProduct]:
        """Return products from the pending store that have a stored price.

        For the webhook-driven flow this returns [], since the webhook
        processes items immediately and removes them. Useful only if you
        manually populate pending records with prices and want to batch-sync.
        """
        results: list[SupplierProduct] = []
        for msg_id, record in self.store.list_pending():
            price = record.get("user_price")
            if price is None:
                # Still waiting for the user — skip.
                continue
            ocr = record.get("ocr", {})
            name = (ocr.get("name") or "").strip()
            if not name:
                continue

            product = self._make_product(
                name=name,
                supplier_url="",
                price=float(price),
                images=[],  # the image is on local disk, not a URL
                stock_status="instock",
                supplier_product_id=ocr.get("sku") or msg_id,
                supplier_category=ocr.get("category_hint") or "",
                original_description=ocr.get("raw_description") or "",
                color=ocr.get("color") or "",
                material=ocr.get("material") or "",
                width=ocr.get("width") or "",
                height=ocr.get("height") or "",
                depth=ocr.get("depth") or "",
                dimensions=ocr.get("dimensions_text") or "",
            )
            # Bypass the price multiplier — WhatsApp prices are final.
            product.calculated_price = float(price)
            results.append(product)

        self.logger.info(f"WhatsAppSupplier yielded {len(results)} pending-with-price products")
        return results
