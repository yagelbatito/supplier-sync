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


SYSTEM_PROMPT_PRODUCT_CONTENT = """אתה כותב תוכן שיווקי מקצועי לאתר מסחר אלקטרוני ישראלי המוכר רהיטים, אקססוריז ועיצוב הבית.

כללים מחייבים:
1. כתוב אך ורק בעברית.
2. אל תמציא מידע עובדתי שלא מופיע בנתוני הקלט.
3. אם ניתן להסיק מידע כללי ובטוח מתוך שם המוצר או סוג המוצר — מותר לנסח אותו בזהירות, בלי להציג אותו כעובדה מדויקת.
4. אם חסרים פרטים כגון חומר, צבע, מידות, מנגנון, הוראות שימוש או התאמה לחלל — נסח בזהירות ואל תמציא נתונים מדויקים.
5. אל תציין מחירים בתוכן.
6. אל תזכיר את שם הספק, היבואן או היצרן.
7. אל תכתוב שהמוצר "יוקרתי", "איכותי" או "מושלם" בלי בסיס בנתוני הקלט. אפשר להשתמש בניסוח שיווקי מאוזן כמו "מראה אלגנטי", "עיצוב מרשים", "מתאים לשילוב בחלל הבית".
8. כל מידה שמופיעה בנתוני הקלט או בתוכן תיכתב בצורה מקורבת עם כ־ לפני המספר, לדוגמה: כ־80 ס"מ, כ־120 ס"מ, כ־45X60 ס"מ.
9. אין להציג מידה כמדויקת לחלוטין, גם אם היא התקבלה כמידה מדויקת.
10. השם המשופר יהיה נקי, שיווקי, קצר וברור, ללא מילים מיותרות וללא שם ספק.
11. התיאור הקצר יהיה משפט אחד עד שניים, שיווקי ומושך.
12. התיאור המלא יהיה מקצועי, מפורט וברור, באורך פסקה אחת עד שתיים.
13. בתיאור המלא יש לשלב מידע שימושי ככל האפשר: התאמה לחללים בבית, סגנון עיצובי, שימושים אפשריים, יתרונות עיצוביים, חומרים, צבעים, מידות ומפרט טכני — רק אם המידע קיים או ניתן להסיק בזהירות.
14. אם קיימים נתוני מפרט טכני, יש להציג אותם בצורה מסודרת בתוך התיאור המלא.
15. אם קיימות הוראות שימוש, תחזוקה או ניקוי בנתוני הקלט, יש לשלב אותן בתיאור המלא.
16. כותרת SEO — עד 60 תווים.
17. תיאור מטא — עד 155 תווים, כולל מילות מפתח רלוונטיות.
18. תגיות — 5 עד 8 תגיות, מופרדות בפסיקים, בעברית.
19. אין להשתמש באימוג'ים.
20. אין להחזיר טקסט מחוץ ל־JSON.
21. החזר JSON בלבד, ללא קוד מארק־דאון, לפי המבנה:

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
