# -*- coding: utf-8 -*-
"""
Per-PRODUCT category classifier (LLM-grounded).

Suppliers classify the same item differently, so mapping a whole supplier
category to one store category is wrong (glasses landed in "ראש השנה", a candle
in "תאורה"). Instead we look at each PRODUCT NAME and pick the CLOSEST existing
store category, grounded in the real store category tree. Only when nothing fits
do we suggest a NEW category under the right parent (for the owner to approve).

Reusable across suppliers: build it from the live store tree + a small set of
owner hints, then call `classify(products)`.

    clf = ProductClassifier(ai, categories=[{"name","parent"}...], hints=[...])
    results = clf.classify([{"sku","name"}...])
    # -> {sku: ClassResult(category, is_new, parent, confidence, reason)}
"""
import json
from dataclasses import dataclass
from typing import Optional

from src.enrichment.keyword_rules import classify_by_rules
from src.core.logger import get_logger

logger = get_logger(__name__)

# Store buckets that are NOT real destinations (sales/collections/manual/vague).
EXCLUDE_CATEGORIES = {
    "70% הנחה", "Gift Card", "New collection", "SALE 70%", "Sale", "Spring",
    "Cote Norie", "אחר", "מוצרים נוספים", "Uncategorized", "ללא קטגוריה",
    "אקססוריז / ציוד נלווה",       # vague catch-all — the model over-uses it
}


@dataclass
class ClassResult:
    category: str                 # chosen EXISTING store category (always set)
    confidence: float = 0.0       # 0..1
    is_new: bool = False          # model thinks a NEW category would fit better
    new_name: str = ""            # suggested new leaf (when is_new)
    parent: str = ""              # parent for the suggested new leaf
    reason: str = ""


_SYSTEM = """אתה ממיין מוצרי עיצוב הבית לקטגוריות של חנות קיימת, לפי שם המוצר בלבד.
עליך לבחור עבור כל מוצר את הקטגוריה **הקיימת** הכי מדויקת מתוך הרשימה שתקבל — ולהיצמד אליה.

⚠️ הרשימה בנויה כ"קבוצה: קטגוריות". category חייב להיות שם של **קטגוריה** מהרשימה (השורות עם •),
לעולם לא שם של קבוצת־על (כמו "יודאיקה", "מטבח ואירוח", "אקססוריז", "ריהוט"). למשל: זוג פמוטים → פמוטים
(לא יודאיקה); נטלה → נטלות; נר → נרות; מראה → מראות.

כללי אצבע (חשוב):
- "מערכת אוכל" / "סרוויס" / "דינר סט" / "מערכת צלחות" → סט צלחות.
- "סט מזון" / כלי הגשה לתה/קפה/סוכר / "תה קפה סוכר" → סטים תה קפה סוכר.
- כל נר (נר, נרון, נר ריחני, נר בישום, נר דקורטיבי) → נרות. לא תאורה!
- מפיץ ריח / מקל ריח / דיפיוזר / ריד דיפיוזר → מפיצי ריח.
- כוס / גביע / סט כוסות (שאינו "כוס קידוש") → כוסות.
- גביע קידוש / כוס קידוש → כוסות קידוש.
- צלחת/צלחות בודדות או סט צלחות → סט צלחות.
- מגש/מגשים → מגשים. ואזה/אגרטל → אגרטלים וואזות. פסל → פסלים.
- קנקן/קומקום/מזיגה → כוסות (כלי מזיגה) אלא אם יש קטגוריה מדויקת יותר.
- קערה/קערות/תבנית/תבניות אפייה/כלי בישול → כלי מטבח.
- פמוט/פמוטים → פמוטים. נטלה/נטלות → נטלות. מזוזה → מזוזות. חנוכיה → חנוכה.
- כאשר שם המוצר תואם כמעט־מדויק לשם קטגוריה קיימת (פמוטים, נרות, מראות, מזוזות, נטלות,
  שעונים, פסלים, מגשים) — בחר **בדיוק** בקטגוריה הזו, אל תמציא חדשה.
- אל תבחר קטגוריה כללית/מעורפלת אם יש קטגוריה מדויקת יותר ברשימה.
- שם המוצר קובע, לא כותרת שיווקית של החג. אל תמיין לפי מילים כמו "לראש השנה" / "מתנה" —
  אלא לפי מה החפץ עצמו (כוסות → כוסות, צלחות → סט צלחות, וכו').

אם אף קטגוריה קיימת לא באמת מתאימה — בחר את הקרובה ביותר בכל זאת (category),
וגם סמן is_new=true עם הצעה: new_name (שם קצר לקטגוריה חדשה) ו-parent (קטגוריית־האם המתאימה מהרשימה).

החזר JSON תקין בלבד בצורה:
{"results":[{"sku":"...","category":"<שם קטגוריה מהרשימה>","confidence":0.0-1.0,"is_new":false,"new_name":"","parent":"","reason":"<קצר>"}, ...]}
- category חייב להיות בדיוק אחד מהשמות ברשימה.
- החזר תוצאה לכל מק"ט שקיבלת, באותו סדר."""


class ProductClassifier:
    def __init__(self, ai_client, categories: list, hints: Optional[list] = None,
                 batch_size: int = 25):
        self.ai = ai_client
        self.batch_size = batch_size
        # categories: list of {"name","parent"} — filter out non-destinations
        self.categories = [c for c in categories
                           if c.get("name") and c["name"] not in EXCLUDE_CATEGORIES]
        self._names = {c["name"] for c in self.categories}
        self.hints = hints or []
        self._cache: dict = {}          # sku -> ClassResult

    @property
    def available(self) -> bool:
        return bool(self.ai and self.ai.available and self.categories)

    def _catalog_text(self) -> str:
        """Category list grouped by parent, so the model sees the hierarchy and
        picks the specific CHILD leaf (e.g. 'פמוטים') rather than the umbrella
        group name (e.g. 'יודאיקה')."""
        groups: dict = {}
        for c in self.categories:
            groups.setdefault(c.get("parent") or "— ראשיות —", []).append(c["name"])
        lines = []
        for parent in sorted(groups):
            lines.append(f"{parent}:")
            for name in sorted(groups[parent]):
                lines.append(f"   • {name}")
        extra = ("\n\nכללים משלימים מבעל החנות:\n" + "\n".join(f"- {h}" for h in self.hints)) if self.hints else ""
        return ("קבוצות → קטגוריות (בחר תמיד קטגוריה מהרשימה, את הספציפית ביותר):\n"
                + "\n".join(lines) + extra)

    def _coerce(self, sku: str, name: str, obj: dict) -> ClassResult:
        cat = str(obj.get("category") or "").strip()
        if cat not in self._names:
            # model returned something off-list → keep it as a NEW suggestion,
            # but we still need an existing home: leave category empty to signal
            # the caller should fall back.
            return ClassResult(category="", confidence=float(obj.get("confidence") or 0),
                               is_new=True, new_name=cat or name[:30],
                               parent=str(obj.get("parent") or ""), reason=str(obj.get("reason") or "off-list"))
        return ClassResult(
            category=cat,
            confidence=float(obj.get("confidence") or 0),
            is_new=bool(obj.get("is_new")),
            new_name=str(obj.get("new_name") or ""),
            parent=str(obj.get("parent") or ""),
            reason=str(obj.get("reason") or ""),
        )

    def classify(self, products: list) -> dict:
        """products: [{"sku","name"}]. Returns {sku: ClassResult}. Cached by sku.

        A deterministic keyword rule decides the obvious names for free; only the
        remainder is sent to the LLM (big token saving on large catalogues)."""
        out: dict = {}
        todo = []
        for p in products:
            sku = p.get("sku") or ""
            if sku in self._cache:
                out[sku] = self._cache[sku]
                continue
            # 1) deterministic keyword rule (no LLM cost)
            cat = classify_by_rules(p.get("name", ""), self._names)
            if cat:
                res = ClassResult(category=cat, confidence=0.95, reason="rule")
                self._cache[sku] = res
                out[sku] = res
            else:
                todo.append(p)

        if todo:
            logger.info(f"classifier: {len(out)} by rule (free), {len(todo)} to LLM")
        catalog = self._catalog_text()
        for i in range(0, len(todo), self.batch_size):
            batch = todo[i:i + self.batch_size]
            listing = "\n".join(f'{p.get("sku")}\t{p.get("name")}' for p in batch)
            user = (f"{catalog}\n\nמיין את המוצרים הבאים (מק\"ט<טאב>שם):\n{listing}")
            raw = self.ai.chat(_SYSTEM, user, max_tokens=1600, temperature=0, json_mode=True)
            parsed = {}
            try:
                data = json.loads(raw)
                for r in data.get("results", []):
                    parsed[str(r.get("sku"))] = r
            except Exception as exc:
                logger.warning(f"classifier: batch {i//self.batch_size} parse failed: {exc}")
            for p in batch:
                sku = p.get("sku") or ""
                res = self._coerce(sku, p.get("name", ""), parsed.get(sku, {}))
                self._cache[sku] = res
                out[sku] = res
            logger.info(f"classifier: {min(i+self.batch_size, len(todo))}/{len(todo)} classified")
        return out
