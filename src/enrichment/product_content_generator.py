"""
Product content enrichment via OpenAI.
Generates improved name, descriptions, SEO fields, and tags.
Only runs for new products or when regenerate_content=True.
"""
import json

from src.core.logger import get_logger
from src.enrichment.openai_client import OpenAIClient
from src.enrichment.prompts import FOOTER_NOTE, SYSTEM_PROMPT_PRODUCT_CONTENT, build_user_prompt
from src.models.product import SupplierProduct

logger = get_logger(__name__)

BAR_CHAIR_TERMS = ("כיסא בר", "כסא בר", "כיסאות בר", "כסאות בר")
BAR_CHAIR_CATEGORY = "כיסאות בר"


class ProductContentGenerator:
    def __init__(self, ai_client: OpenAIClient):
        self._ai = ai_client

    @property
    def available(self) -> bool:
        return self._ai.available

    def enrich(self, product: SupplierProduct) -> SupplierProduct:
        """
        Enrich product content via OpenAI.
        Mutates and returns the same product object.
        On failure, returns product unchanged.
        """
        if not self._ai.available:
            logger.debug(f"AI unavailable, skipping enrichment for: {product.name}")
            return product

        logger.info(f"Enriching: {product.name[:60]}")

        user_prompt = build_user_prompt(product)
        raw = self._ai.chat(
            system_prompt=SYSTEM_PROMPT_PRODUCT_CONTENT,
            user_prompt=user_prompt,
            max_tokens=1200,
            temperature=0.7,
            json_mode=True,
        )

        if not raw:
            logger.warning(f"Empty AI response for: {product.name}")
            return product

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(f"AI response JSON parse error: {exc} | raw={raw[:200]}")
            return product

        # Apply enriched content — new schema (improved_name / meta_description / footer_note)
        # with backward-compat keys so older cached responses don't fall over.
        if data.get("improved_name") or data.get("improved_product_name"):
            improved_name = (data.get("improved_name") or data.get("improved_product_name")).strip()
            product.improved_name = self._safe_improved_name(product, improved_name)

        if data.get("short_description"):
            product.short_description = data["short_description"].strip()

        # AI provides only the product description. The marketing footer is a
        # fixed template appended in code — keeps it out of the prompt to save
        # tokens, and lets us edit the contact info in one place.
        full_desc = (data.get("full_description") or "").strip()
        if full_desc:
            product.full_description = full_desc + "\n\n" + FOOTER_NOTE
        else:
            product.full_description = FOOTER_NOTE

        if data.get("seo_title"):
            product.seo_title = data["seo_title"].strip()[:60]

        meta_desc = data.get("meta_description") or data.get("seo_meta_description")
        if meta_desc:
            product.seo_meta_description = meta_desc.strip()[:155]

        # tags can arrive as either a comma-separated string or a list
        raw_tags = data.get("tags")
        if isinstance(raw_tags, str):
            product.tags = [t.strip() for t in raw_tags.split(",") if t.strip()][:8]
        elif isinstance(raw_tags, list):
            product.tags = [str(t).strip() for t in raw_tags if t][:8]

        product.ai_generated = True
        logger.info(f"Enriched: {product.improved_name or product.name}")
        return product

    @staticmethod
    def _safe_improved_name(product: SupplierProduct, improved_name: str) -> str:
        """Prevent AI from adding "כיסא בר" when the source product is not one."""
        improved_l = improved_name.lower()
        original_l = product.name.lower()
        ai_added_bar_chair = (
            any(term in improved_l for term in BAR_CHAIR_TERMS)
            and not any(term in original_l for term in BAR_CHAIR_TERMS)
        )
        if ai_added_bar_chair and product.mapped_category != BAR_CHAIR_CATEGORY:
            logger.warning(
                f"AI added bar-chair wording to non-bar product: "
                f"{product.name!r} → {improved_name!r}; keeping original name"
            )
            return product.name
        return improved_name
