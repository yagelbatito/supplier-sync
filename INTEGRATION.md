# INTEGRATION.md — חיבור התוסף לפרויקט הקיים

המדריך הזה מסביר בדיוק איפה כל קובץ הולך ואיזה שינויים קטנים אתה צריך
לעשות בקוד הקיים שלך (`supplier-sync`).

## תקציר

החבילה הזו מוסיפה ערוץ קלט חדש (WhatsApp) למערכת. **שום קובץ קיים לא נשבר**.
היא רק:

1. מוסיפה תיקייה חדשה `src/whatsapp/` (6 קבצים)
2. מוסיפה קובץ אופציונלי `src/suppliers/whatsapp_supplier.py`
3. מוסיפה `whatsapp_listener.py` בשורש
4. מצריכה ממך לחבר 2 imports בתוך `whatsapp_listener.py` (השמות של ה-modules הקיימים שלך)
5. מצריכה ממך למלא פונקציה אחת ב-`src/whatsapp/orchestrator.py` (`_run_enrichment`)

---

## שלב 1 — העתק קבצים

מהזיפ של המשלוח הזה, העתק את הקבצים האלה אל הפרויקט הקיים שלך:

```
supplier-sync/
├── src/
│   ├── whatsapp/                       ← תיקייה חדשה לגמרי
│   │   ├── __init__.py
│   │   ├── meta_client.py
│   │   ├── ocr_service.py
│   │   ├── message_parser.py
│   │   ├── pending_store.py
│   │   ├── orchestrator.py
│   │   └── webhook_server.py
│   │
│   └── suppliers/
│       └── whatsapp_supplier.py        ← אופציונלי (לא חובה להעתיק)
│
├── whatsapp_listener.py                 ← entry point — בשורש הפרויקט
├── requirements_whatsapp.txt            ← לצרף ל-requirements.txt הקיים
└── data/
    └── whatsapp_images/                 ← נוצרת אוטומטית בהרצה ראשונה
```

---

## שלב 2 — תלויות

```bash
pip install -r requirements_whatsapp.txt
```

מתקין: `fastapi`, `uvicorn`, `httpx`. החבילות האחרות (`openai`, `requests`,
`python-dotenv`) כבר אצלך.

---

## שלב 3 — `.env`

הוסף את התוכן של `.env.whatsapp.example` לסוף ה-`.env` הקיים שלך.
**מינימום נדרש להתחיל:**

```env
WHATSAPP_TOKEN=...                       # מ-Meta Developer Dashboard
WHATSAPP_PHONE_NUMBER_ID=...             # מ-Meta Developer Dashboard
WHATSAPP_VERIFY_TOKEN=any_random_string  # אתה ממציא; שים זהה גם ב-Meta
WHATSAPP_ALLOWED_NUMBERS=972501234567    # רק אתה — מספרים אחרים יידחו
WHATSAPP_APP_SECRET=...                  # מ-Meta App → Basic Settings
```

---

## שלב 4 — חיבור ה-imports ב-`whatsapp_listener.py`

פתח את `whatsapp_listener.py`. יש שני TODO[wire] שאתה צריך לאמת:

### 4.1 — `load_suppliers_config`

הקוד מנסה:

```python
from src.core.config_loader import load_suppliers_config
```

ה-suppliers.json שלך נטען איפשהו במקור. פתח את `main.py` ותראה איך
טוענים את הקובץ. עדכן את הimport בהתאם — למשל אם הפונקציה נקראת
`get_suppliers()` או `ConfigLoader().load_all()`, תחליף בהתאם בשורה
שכתוב `# TODO[wire]`.

**הציפיה**: שהפונקציה תחזיר `dict[str, SupplierConfig]` — מפתח לפי `key`
של הספק (כמו ב-`suppliers.json`).

### 4.2 — WC services

הקוד מנסה:

```python
from src.woocommerce.client import WooCommerceClient
from src.woocommerce.media_service import MediaService
from src.woocommerce.product_service import ProductService
```

לפי הקבצים ששלחת זה אמור להיות בדיוק כך. אבל ה-`WooCommerceClient()` כתבתי
שיקרא env vars מעצמו — אם הוא דורש פרמטרים, תוסיף אותם בקריאה ב-
`whatsapp_listener.py` → `build_orchestrator()`.

---

## שלב 5 — חיבור ה-OpenAI enrichment

זה הצעד הקריטי. פתח את `src/whatsapp/orchestrator.py`, לך לפונקציה
`_run_enrichment()`, וחבר אותה לפונקציה הקיימת שלך.

הקוד הנוכחי הוא `raise NotImplementedError` עם הוראות. תחליף ב:

```python
def _run_enrichment(self, product: SupplierProduct) -> None:
    # זה הimport שאתה משתמש בו ב-main.py עבור הספקים האחרים:
    from src.ai.openai_client import enrich_product   # ← התאם לשם האמיתי
    enrich_product(product)
```

**איך לדעת מה לכתוב**: פתח את `main.py` שלך, חפש "openai" או "enrich",
תראה איך אתה קורא לזה היום עבור `paldinox` / `perlahome` / `floralis`,
והעתק את אותה השורה.

הציפייה היא ש-`enrich_product()` יעדכן את ה-`product` in-place ויגדיר:
`name` (מלוטש), `description` / `final_description()`, `short_description`,
`seo_title`, `seo_meta_description`, `tags`, `ai_generated=True`.

---

## שלב 6 — קטגוריות (אופציונלי)

ב-`orchestrator.py → _upsert_in_woocommerce()` יש שורה:

```python
category_ids: list[int] = []
```

אם יש לך resolver שממפה `supplier_category` ל-WC category IDs (לפי הזרימה
הקיימת ב-main.py שלך), חבר אותו שם. אם לא — הקטגוריה תיקבע על ידי
`default_category` של הספק ב-`suppliers.json`, או דרך השדה `tags` ש-
ה-enrichment מגדיר.

---

## שלב 7 — חשיפת השרת לאינטרנט

Meta דורש שרת ציבורי עם HTTPS. שלוש אופציות:

**א. ngrok (לפיתוח/בדיקות, חינם):**
```bash
# טרמינל 1
python whatsapp_listener.py

# טרמינל 2
ngrok http 8000
# העתק את ה-URL: https://abcd1234.ngrok-free.app
```

**ב. Cloudflare Tunnel (חינם גם לפרודקשן):**
```bash
cloudflared tunnel --url http://localhost:8000
```

**ג. VPS עם nginx + Let's Encrypt** (לפרודקשן רציני, ~5$/חודש ב-Hetzner).

---

## שלב 8 — חיבור ה-webhook ב-Meta Dashboard

1. https://developers.facebook.com → האפליקציה שלך → WhatsApp → Configuration
2. **Callback URL**: `https://YOUR_DOMAIN/webhook/whatsapp`
3. **Verify Token**: בדיוק המחרוזת ששמת ב-`WHATSAPP_VERIFY_TOKEN` ב-.env
4. לחץ "Verify and Save" — אם הקוד רץ ומקבל, Meta יראה תיק ירוק.
5. תחת "Webhook fields" → סבסקרייב ל-`messages`.

---

## שלב 9 — בדיקה מקצה לקצה

1. שלח לעצמך תמונה של מוצר ל-WhatsApp Business שלך.
2. הבוט אמור לענות תוך 5-15 שניות (זמן OCR) עם סיכום של מה שזיהה.
3. ענה לאותה הודעה: `ספק:perlahome מחיר:150`
4. הבוט אמור לענות תוך 3-10 שניות: `✅ created — WC ID: 1234`
5. בדוק שהמוצר באמת בלוח הבקרה של WC.

---

## פתרון בעיות נפוצות

| תופעה | סיבה אפשרית |
|---|---|
| "Verification failed" ב-Meta | `WHATSAPP_VERIFY_TOKEN` ב-.env ובדאשבורד לא זהים |
| Meta שולח הודעה ולא קורה כלום | המספר שלך לא ב-`WHATSAPP_ALLOWED_NUMBERS` |
| OCR מחזיר ריק | התמונה לא ברורה, או `OPENAI_API_KEY` לא תקין |
| `NotImplementedError` בלוג | לא חיברת את `_run_enrichment` בשלב 5 |
| "WC services לא מחוברים" | imports נכשלו ב-`build_orchestrator()` — ראה הלוג |
| `SKU already exists` ב-WC | `find_by_sku` כבר מטפל גם ב-trash; אם זה קורה — תבדוק ידנית במסד |
| תמונה לא עולה ל-WC | בדוק שה-MediaService שלך חושף `_client.upload_media(bytes, name, mime)` |

---

## איך לבדוק שזה רץ בלי לחבר את Meta

הפעל מבחן יחידה ידני על ה-OCR:

```python
import os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()

from src.whatsapp.ocr_service import OcrService

with open("test_image.jpg", "rb") as f:
    bytes_ = f.read()

ocr = OcrService()
result = ocr.extract(bytes_)
print(result.display_summary())
print("Confidence:", result.confidence)
```

אם זה מציג שם של מוצר ומק"ט — ה-OCR עובד. עכשיו רק חסר לחבר webhook.

---

## ארכיטקטורה — איך הכל מתחבר

```
                ┌─────────────────────────┐
                │  WhatsApp Cloud API     │
                │  (Meta)                 │
                └─────────┬───────────────┘
                          │ POST /webhook/whatsapp
                          ▼
          ┌──────────────────────────────┐
          │  webhook_server.py (FastAPI) │
          │  - אימות חתימה               │
          │  - allowlist                 │
          │  - BackgroundTasks           │
          └──────────────┬───────────────┘
                         ▼
          ┌──────────────────────────────┐
          │  orchestrator.py             │
          │                              │
          │  image  → ocr_service → store │
          │  text   → parser → enrich →  │
          │           WC upsert          │
          └──┬──────┬──────┬──────┬──────┘
             │      │      │      │
             ▼      ▼      ▼      ▼
        meta_      ocr_   pending_  ProductService
        client    service  store    (קיים!)
                                    │
                                    ▼
                                  WooCommerce
```

המודולים החדשים מתחברים לקוד הקיים שלך **רק** ב-`orchestrator.py`:
שם הוא קורא ל-`product_service.create/update()`, ל-`media_service`,
ול-`_run_enrichment()` שאתה ממלא.

---

## מה הלאה (שיפורים אפשריים)

- **תור פגי תוקף**: `PendingStore.cleanup_expired()` קיים, אבל לא רץ אוטומטית.
  אפשר להוסיף background task ב-FastAPI שירוץ כל שעה.
- **אישור לפני יצירה**: להוסיף state `awaiting_confirmation` ב-PendingStore
  ולשלוח "האם ליצור? כן/לא" לפני שזה עולה ל-WC.
- **תמיכה במק"ט קיים מ-WC**: כשמ-OCR מחלץ מק"ט שכבר קיים באתר, להציע
  פעולת "עדכון בלבד" במקום ליצור חדש (זה כבר קורה, אבל בלי הודעה
  ל-WhatsApp שמכוונת אותך).
- **קטגוריה אוטומטית מ-`category_hint`**: אם ה-OCR החזיר "ספה", למפות
  אוטומטית ל-`product-category=ספות` ב-WC.
