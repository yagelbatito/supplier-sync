# -*- coding: utf-8 -*-
"""
Golyan → store category mapping (phase 1).

Central place for ALL Golyan mapping rules so new rules can be added here
without touching the sync mechanism. Only the categories in the owner's phase-1
spec are handled; anything else is returned UNMAPPED (never guessed).

`map_product(name, source_cats)` → MapResult(target, is_furniture, rule) where
`target` is the leaf WC category name, or None when nothing matched.

Priority (owner spec §4): specific name rule → attribute (bin volume) →
sub-category → source category → general.
"""
import re
from dataclasses import dataclass
from typing import Optional

# Target leaf categories that count as FURNITURE → price ×0.70 (−30%);
# everything else → ×0.75 (−25%).
FURNITURE_TARGETS = {
    "שולחנות סלון", "שולחנות צד", "הדומים",
    "קונסולות ושידות כניסה", "מראות", "ספריות",
}

# Categories to CREATE if missing (leaf, parent). Nothing else is auto-created.
NEW_CATEGORIES = [
    ("מעמדים", "אקססוריז"),
    ("מגשים", "אקססוריז"),
    ("מלחיות", "מטבח ואירוח"),
    ("הבדלה ומחלקי יין", "יודאיקה"),
    ("ברכות", "יודאיקה"),
]


@dataclass
class MapResult:
    target: Optional[str]
    is_furniture: bool
    rule: str          # short label of the matched rule (or "" when unmapped)


def _liters(name: str) -> Optional[int]:
    """Extract a bin volume in liters from the name, if present."""
    for pat in (r"(\d+(?:\.\d+)?)\s*ליטר", r"(\d+(?:\.\d+)?)\s*ליט",
                r"(\d+(?:\.\d+)?)\s*ל[\"'’]", r"(\d+(?:\.\d+)?)\s*l\b"):
        m = re.search(pat, name, flags=re.IGNORECASE)
        if m:
            try:
                return int(float(m.group(1)))
            except ValueError:
                pass
    return None


def _has(name: str, *terms: str) -> bool:
    return any(t in name for t in terms)


def map_product(name: str, source_cats: Optional[list] = None) -> MapResult:
    n = " ".join((name or "").split())            # normalize whitespace
    cats = set(source_cats or [])

    def R(target, rule):
        return MapResult(target, target in FURNITURE_TARGETS, rule)

    # ── 1. BINS — volume/keyword attribute rule (highest priority) ──
    is_toilet_brush = _has(n, "מברשת לשירותים", "מברשת לאסלה", "מברשת שירותים", "מברשת אסלה")
    if _has(n, "פח") or is_toilet_brush:
        vol = _liters(n)
        if is_toilet_brush or (vol is not None and vol <= 7):
            return R("פחים ומברשות לשירותים", "bin≤7L / toilet-brush")
        if vol is not None and vol >= 20:
            return R("פחים", "bin≥20L")
        # 7<vol<20 or unknown volume → let it fall through (may match nothing)

    # ── 2. ריהוט משלים (name rules; furniture) ──
    if _has(n, "סט שולחנות", "זוג שולחנות") or ("שולחן" in n and "נשכן" in n):
        return R("שולחנות סלון", "סט/זוג שולחנות / שולחן+נשכן")
    if _has(n, "שולחן צד", "נשכן", "סטול", "פודיום"):
        return R("שולחנות צד", "שולחן צד/נשכן/סטול/פודיום")
    if _has(n, "הדום", "הדומים"):
        return R("הדומים", "הדום")
    if _has(n, "קונסולה", "קונסולות", "קונזולה"):
        return R("קונסולות ושידות כניסה", "קונסולה")
    if _has(n, "מראה", "מראות", "מראת"):
        return R("מראות", "מראה")
    if _has(n, "ויטרינה", "ויטרינת", "ויטרינות", "ספריות", "ספרייה"):
        return R("ספריות", "ויטרינה/ספריות")

    # ── 3. אקססוריז ──
    # acrylic blocks (must beat the generic מעמדים rule)
    if _has(n, "מעמד אקריליק", "מעמדי אקריליק"):
        return R("בלוקים אקריליים", "מעמד אקריליק")
    # judaica-specific stands (beat מעמדים)
    if _has(n, "מעמד יין", "מעמדי יין", "מחלק יין", "מחלקי יין", "סט הבדלה",
            "הבדלה", "מתקן לגפרורים"):
        return R("הבדלה ומחלקי יין", "הבדלה/מחלק יין")
    if _has(n, "מעמד סט 6 ברכות", "סט ברכות", "ברכונים", "ברכות"):
        return R("ברכות", "ברכות/ברכונים")
    if _has(n, "עגלת תה", "עגלות תה", "עגלת מטבח", "עגלות מטבח", "עגלת משקאות"):
        return R("עגלת משקאות", "עגלת תה/משקאות")
    # planters / pot-holders BEFORE plants
    if _has(n, "בית עציץ", "בתי עציץ", "סט שלושה בתי עציץ", "זוג בתי עציץ",
            "סט 3 מעמדים לעציצים", "סט 3 ואזות"):
        return R("בתי עציץ", "בית עציץ")
    if _has(n, "מעמד נר רצפתי", "מעמד לעציצים", "מעמד לעציץ"):
        return R("מעמדים", "מעמד נר/עציצים")
    if _has(n, "ואזה", "ואזות", "וזה קרמיקה", "ואזה קרמיקה", "ואזות קרמיקה"):
        return R("אגרטלים וואזות", "ואזה")
    if _has(n, "שעון", "שעונים"):
        return R("שעונים", "שעון")
    if _has(n, "פסל נוי", "פסלי נוי", "פסל", "פסלים"):
        return R("פסלים", "פסל")
    if _has(n, "מגש", "מגשים"):
        return R("מגשים", "מגש")
    if _has(n, "תאורה משלימה") or ("תאורה" in cats) or ("תאורת קישוט" in cats):
        return R("תאורה", "תאורה")

    # ── 4. מטבח ואירוח ──
    if _has(n, "מלחיה", "מלחיות", "סט מלחיות"):
        return R("מלחיות", "מלחיה")

    # ── 5. יודאיקה ──
    if _has(n, "גביע קידוש", "כוס קידוש", "כוס חתנים", "כוס חתן", "גביע חתן"):
        return R("כוסות קידוש", "כוס/גביע קידוש")
    if _has(n, "כיסוי לפלטה", "כיסויים לפלטה", "כיסוי פלטה"):
        return R("כיסוי לפלטה", "כיסוי פלטה")
    if _has(n, "חנוכיה", "חנוכייה", "חנוכה", "מנורת חנוכה") or ("חנוכה" in cats):
        return R("חנוכה", "חנוכה")

    # ── 6. צמחים (real plants / decorative trees; AFTER planters/stands) ──
    if (_has(n, "עציץ", "צמח", "פרח", "בונסאי", "סוקולנט", "קקטוס")
            or n.startswith("עץ ") or _has(n, "עץ דקורטיבי")
            or ("פרחים" in cats) or ("פרחי קישוט" in cats)):
        return R("צמחים", "צמח/עץ/פרח")

    # ── 7. אמבטיה (source-category fallback) ──
    if ("אביזרי אמבטיה" in cats) or _has(n, "אביזרי אמבטיה"):
        return R("אביזרי אמבטיה", "source: אביזרי אמבטיה")

    return MapResult(None, False, "")            # UNMAPPED
