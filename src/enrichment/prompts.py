"""
OpenAI prompt templates for product content generation.
Prompts are in Hebrew, targeting Israeli furniture e-commerce.
"""

# A fixed marketing footer appended to every product description.
# Kept OUT of the OpenAI prompt to save ~30% of input tokens per product.
# Edit this string freely — changes take effect on the next product enrichment.
FOOTER_NOTE = """במידה ומצאתם מחיר נמוך יותר, תוכלו ליצור איתנו קשר בוואטסאפ בלבד עם תמונה של המוצר, ונשתדל להשוות ולעשות הנחה נוספת במידת האפשר.

לכל שאלה נוספת ניתן לפנות אלינו בוואטסאפ או בטלפון במספר 050-5766659.

אם אין מענה, נא להשאיר הודעת וואטסאפ ונחזור אליכם בהקדם.

החנות הפיזית שלנו נמצאת בכתובת:
כוכב הצפון 8, אשדוד.

שעות פעילות:
ימים א׳-ה׳ 09:00-19:30
יום ו׳ 09:00-13:30
יום שבת סגור

חניה בשפע.
משלוחים לכל הארץ."""


SYSTEM_PROMPT_PRODUCT_CONTENT = """אתה איש מכירות ותוכן מנוסה לאתר ישראלי לרהיטים ועיצוב הבית. כתוב בעברית בלבד, בביטחון ובמקצועיות, כדי למכור.

כללים:
1. ביטחון מלא, ללא הסתייגות. אסורות המילים: "ניתן להניח", "כנראה", "ייתכן", "נראה ש", "אמור", "כפי הנראה", "יכול להיות". במקום "ניתן להניח שהחומר עמיד" — "עשוי מחומר איכותי ועמיד".
2. בסס על שם המוצר, התיאור וסוגו, והיעזר בידע כללי על מוצרים דומים. שפה שיווקית חיובית (איכותי, עמיד, אלגנטי, יוקרתי, מתאים לכל בית) — מותרת ורצויה.
3. אל תמציא נתונים מספריים/טכניים (מידות, משקל, הרכב, תקנים, אחריות) שלא הופיעו בקלט; תכונות איכות כלליות מותר בביטחון.
4. בלי מחירים ובלי שם ספק/יבואן/יצרן. מידה מהקלט תיכתב מקורבת עם כ־ (כ־80 ס"מ).
5. improved_name: נקי, שיווקי, קצר, ללא שם ספק. short_description: משפט-שניים מושכים. full_description: פסקה-שתיים משכנעות וזורמות — מה המוצר, למי, לאילו חללים ושימושים, יתרונות עיצוב ואיכות, חומרים וסגנון; שלב מפרט/הוראות תחזוקה אם קיימים בקלט.
6. seo_title ≤60 תווים. meta_description ≤155 תווים עם מילות מפתח. tags: 5–8 תגיות בעברית מופרדות בפסיקים. בלי אימוג'ים.
7. החזר JSON בלבד לפי המבנה:

{"improved_name":"","short_description":"","full_description":"","seo_title":"","meta_description":"","tags":""}"""

USER_PROMPT_PRODUCT_TEMPLATE = """נתוני המוצר:
שם מוצר: {name}
קטגוריה: {category}
תיאור מקורי: {description}
חומר: {material}
צבע: {color}
מידות: רוחב {width} | עומק {depth} | גובה {height}
קישור ספק: {url}

החזר JSON בלבד לפי המבנה שהוגדר ב־system prompt."""


def build_user_prompt(product) -> str:
    """Build the user prompt from a SupplierProduct."""
    from src.core.utils import truncate
    return USER_PROMPT_PRODUCT_TEMPLATE.format(
        name=product.name,
        category=product.mapped_category or product.supplier_category or "לא ידועה",
        description=truncate(product.original_description, 800),
        material=product.material or "לא צוין",
        color=product.color or "לא צוין",
        width=product.width or "לא צוין",
        depth=product.depth or "לא צוין",
        height=product.height or "לא צוין",
        url=product.supplier_url,
    )
