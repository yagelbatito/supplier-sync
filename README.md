# supplier-sync · WhatsApp module

תוסף ל-`supplier-sync` הקיים שמאפשר העלאת מוצרים מתמונות וואטסאפ ישירות
ל-WooCommerce, באמצעות אותו pipeline שכבר עובד עבור הספקים שלך
(paldinox, perlahome, floralis וכו').

## הזרימה

```
1. אתה שולח/מקבל בוואטסאפ תמונה של מוצר עם פרטים כתובים עליה
       ↓
2. Webhook FastAPI מקבל את ההודעה מ-Meta Cloud API
       ↓
3. GPT-4o Vision מחלץ JSON: שם, מק"ט, תיאור גולמי, מידות, חומר, צבע
       ↓
4. הבוט עונה בוואטסאפ: "✅ זיהיתי את X. ענה: ספק:Y מחיר:Z"
       ↓
5. אתה עונה (reply) להודעה עם פרטי המוצר, למשל:
   "שם: שולחן צד מידות: 40x40x55 מחיר: 390 קטגוריה: שולחנות צד"
       ↓
6. SupplierProduct נבנה ומועבר ל-pipeline הקיים:
   • OpenAI enrichment (תיאור מפורט, meta description, תגיות)
   • ProductService.find_by_sku → create / update
   • MediaService מעלה את תמונת הוואטסאפ כתמונה ראשית
       ↓
7. הבוט עונה: "✅ CREATED. WC ID: 1234"
```

## איך זה משתלב עם מה שכבר יש לך

המודול לא משנה את הקוד הקיים. הוא:
- מוסיף `src/whatsapp/` (תיקייה חדשה)
- מוסיף `src/suppliers/whatsapp_supplier.py` (יורש מ-`BaseSupplier` כמו שאר הספקים)
- מוסיף ערך אחד ל-`suppliers.json` (עם `enabled: false` כדי לא להריץ scrape ב-cron)
- מצריך 2 שינויים של ~5 שורות ב-`media_service.py` כדי לקרוא קבצים מקומיים

הצינור עצמו (enrichment + WC create/update) הוא בדיוק אותו צינור שכבר רץ אצלך.

## תוכן הזיפ

```
.
├── README.md                            ← הקובץ הזה
├── INTEGRATION.md                       ← מדריך חיבור צעד-אחר-צעד
├── requirements_whatsapp.txt            ← תלויות לתוסף
├── .env.whatsapp.example                ← משתני סביבה חדשים שצריך
├── whatsapp_listener.py                 ← entry point (תריץ אותו עם python)
│
├── src/
│   ├── whatsapp/
│   │   ├── __init__.py
│   │   ├── meta_client.py               ← Meta Cloud API client
│   │   ├── ocr_service.py               ← GPT-4o Vision extraction
│   │   ├── message_parser.py            ← parsing "ספק:X מחיר:Y"
│   │   ├── pending_store.py             ← JSON persistence
│   │   ├── orchestrator.py              ← main flow logic
│   │   └── webhook_server.py            ← FastAPI app
│   │
│   └── suppliers/
│       └── whatsapp_supplier.py         ← BaseSupplier subclass adapter
│
└── patches/
    ├── suppliers.json.patch             ← ערך להוסיף ל-suppliers.json
    └── media_service.py.patch           ← 2 שינויים קטנים ב-media_service.py
```

## פורמט הודעה מומלץ

אפשר לשלוח את הפרטים כ-caption יחד עם התמונה, או כתשובה להודעת הבוט:

```
שם: שולחן צד מידות: 40x40x55 מחיר: 390 קטגוריה: שולחנות צד
```

אפשר גם לפרט מידות בנפרד:

```
שם: כורסה מעוצבת רוחב: 80 עומק: 75 גובה: 90 מחיר: 1290 קטגוריה: כורסאות
```

אם רוצים לשייך לספק קיים במקום לספק הידני הפנימי:

```
ספק:perlahome מחיר:390 שם: שולחן צד מידות: 40x40x55 קטגוריה: שולחנות צד
```

## להתחיל

קרא את `INTEGRATION.md`. שם הכל מפורט.

## תכונות

- אבטחה: רק מספרי טלפון מורשים יכולים להעלות מוצרים (`WHATSAPP_ALLOWED_NUMBERS`)
- אימות חתימה: X-Hub-Signature-256 (אופציונלי בפיתוח, חובה בפרודקשן)
- Idempotency: SKU יציב לפי `whatsapp://message/{id}` — לא ייווצרו כפילויות
- Resilience: כשלון בכל שלב לא קוטע את ה-webhook — Meta לא ינסה שוב לשלוח
- Reply context: התגובה למחיר מזוהה לפי reply context או לפי "האחרון שנשלח"
- עברית מלאה: OCR ופרסר תומכים בעברית ואנגלית
- TTL: פריטים שמחכים למחיר נמחקים אוטומטית אחרי שבוע
