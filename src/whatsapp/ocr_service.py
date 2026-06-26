"""
GPT-4o Vision OCR — extracts structured product fields from an image.

The user's WhatsApp images have product details printed on the image itself
(supplier's product card / catalog page). We ask GPT-4o to return strict JSON
with the fields that map cleanly into SupplierProduct.

Why GPT-4o and not Tesseract/Google Vision?
- The text is laid out as a marketing card (logo + photo + labeled rows),
  not flat document text. Layout-aware extraction beats raw OCR + regex.
- We're already paying OpenAI for the enrichment step — one model, one bill.
- Hebrew handling out of the box.
"""
import base64
import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI

from src.core.logger import get_logger

logger = get_logger(__name__)


# The prompt is opinionated: we tell the model exactly what schema we need,
# and to leave fields empty rather than guess. We've also seen GPT-4o
# occasionally wrap output in ```json fences — we strip those defensively.
_SYSTEM_PROMPT = """\
You are an OCR and information extraction service for a Hebrew furniture/home-goods store.
The image is a product card or catalog page from a supplier. Text may appear in Hebrew or English.

Extract the product details and return a SINGLE JSON object — no prose, no markdown fences.

Schema (all fields are strings, use "" when not visible):
{
  "name":               product display name as printed,
  "sku":                supplier SKU / model / catalog number / item code,
  "raw_description":    any descriptive paragraph or bullet points (verbatim, keep line breaks as spaces),
  "color":              primary color (Hebrew if Hebrew used on image),
  "material":           primary material,
  "width":              width with units, e.g. "60 ס\\"מ" or "60 cm",
  "height":             height with units,
  "depth":              depth / length with units,
  "dimensions_text":    free-form dimensions block if present, e.g. "60x40x90 ס\\"מ",
  "brand":              brand/manufacturer if visible,
  "category_hint":      product category if explicitly stated (e.g. "ספה", "שולחן"),
  "confidence":         "high" | "medium" | "low"  — your confidence in name+sku extraction
}

Rules:
- If the image is NOT a product card (e.g. just a photo with no text, or unrelated content), return
  {"name":"","sku":"","raw_description":"","color":"","material":"","width":"","height":"","depth":"","dimensions_text":"","brand":"","category_hint":"","confidence":"low"}
- Do NOT translate. Keep Hebrew as Hebrew.
- Do NOT invent values. Empty string is better than a guess.
- Return ONLY the JSON object, nothing else.
"""


@dataclass
class OcrResult:
    """Structured output of OCR — maps 1:1 to SupplierProduct fields."""
    name: str = ""
    sku: str = ""
    raw_description: str = ""
    color: str = ""
    material: str = ""
    width: str = ""
    height: str = ""
    depth: str = ""
    dimensions_text: str = ""
    brand: str = ""
    category_hint: str = ""
    confidence: str = "low"
    # Raw model response, kept for debugging / pending store inspection.
    raw_json: dict = field(default_factory=dict)

    def is_usable(self) -> bool:
        """We need at least a name to proceed. SKU is nice-to-have."""
        return bool(self.name.strip())

    def display_summary(self) -> str:
        """Short, WhatsApp-friendly summary of what we extracted."""
        parts: list[str] = []
        if self.name:
            parts.append(f"שם: {self.name}")
        if self.sku:
            parts.append(f"מק\"ט: {self.sku}")
        if self.color:
            parts.append(f"צבע: {self.color}")
        if self.dimensions_text:
            parts.append(f"מידות: {self.dimensions_text}")
        elif any([self.width, self.height, self.depth]):
            dims = " × ".join(filter(None, [self.width, self.height, self.depth]))
            parts.append(f"מידות: {dims}")
        return "\n".join(parts)


class OcrService:
    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None):
        self.model = (model or os.getenv("WHATSAPP_OCR_MODEL", "gpt-4o")).strip()
        key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        if not key:
            raise RuntimeError("OPENAI_API_KEY not configured")
        self._client = OpenAI(api_key=key)

    def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> OcrResult:
        """Run OCR. Always returns an OcrResult — never raises on parse errors."""
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime_type};base64,{b64}"

        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                # response_format=json_object isn't supported on all vision-capable
                # models; we ask for JSON in the prompt and parse defensively.
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Extract the product details from this image."},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
                temperature=0.0,
                max_tokens=800,
            )
            content = (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.error(f"OCR API call failed: {exc}")
            return OcrResult()

        # Strip ```json fences defensively.
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        try:
            data = json.loads(content)
        except Exception as exc:
            logger.error(f"OCR returned non-JSON output: {exc}\nContent: {content[:300]}")
            return OcrResult()

        return OcrResult(
            name=(data.get("name") or "").strip(),
            sku=(data.get("sku") or "").strip(),
            raw_description=(data.get("raw_description") or "").strip(),
            color=(data.get("color") or "").strip(),
            material=(data.get("material") or "").strip(),
            width=(data.get("width") or "").strip(),
            height=(data.get("height") or "").strip(),
            depth=(data.get("depth") or "").strip(),
            dimensions_text=(data.get("dimensions_text") or "").strip(),
            brand=(data.get("brand") or "").strip(),
            category_hint=(data.get("category_hint") or "").strip(),
            confidence=(data.get("confidence") or "low").strip().lower(),
            raw_json=data,
        )
