# הבוט של הוואטסאפ — 24/7 בלי המחשב שלך

מטרה: להעלות מוצרים בוואטסאפ **מכל טלפון שלך, מתי שרוצים**, בלי שהמחשב יהיה דלוק.
3 שלבים. כל השאר כבר מוכן בקוד.

---

## שלב 1 — טוקן קבוע (שלא פג כל 24 שעות)
כרגע הטוקן זמני. ניצור "System User Token" קבוע.

1. היכנס ל‑https://business.facebook.com/settings → בחר את העסק שלך.
2. בתפריט: **Users → System Users** → **Add** → שם: `whatsapp-bot`, תפקיד **Admin** → Create.
3. לחץ על המשתמש שנוצר → **Add Assets** → **Apps** → בחר את `smadar-shop` → תן הרשאה **Manage** (Full control) → Save.
4. לחץ **Generate new token** → בחר את האפליקציה `smadar-shop`.
5. סמן את ההרשאות: **whatsapp_business_messaging** ו‑**whatsapp_business_management**.
6. תוקף (Expiration): **Never**.
7. **Generate token** → העתק את הטוקן הארוך (זה קבוע!) ושמור אותו לרגע.

---

## שלב 2 — העלאה לאחסון בענן (Render, חינם)
1. היכנס ל‑https://render.com → **Sign up** (הכי קל: "Sign in with GitHub", עם אותו חשבון שהמאגר supplier-sync נמצא בו).
2. לחץ **New +** → **Blueprint**.
3. בחר את המאגר **supplier-sync** → Render יזהה את הקובץ `render.yaml` אוטומטית.
4. Render יבקש למלא את המשתנים הסודיים (Environment Variables). מלא אותם מהקובץ `.env` המקומי שלך:

| שם המשתנה | הערך |
|---|---|
| `WHATSAPP_TOKEN` | **הטוקן הקבוע** משלב 1 |
| `WHATSAPP_PHONE_NUMBER_ID` | `1189245404274322` |
| `WHATSAPP_VERIFY_TOKEN` | `smdr_7fcdb62248421fc6533d1232` |
| `WHATSAPP_ALLOWED_NUMBERS` | הטלפונים שלך, מופרד בפסיק. למשל `972505766659` (אפשר להוסיף עוד) |
| `OPENAI_API_KEY` | מ‑`.env` |
| `WOOCOMMERCE_URL` | `https://smadarbetitohome.co.il` |
| `WOOCOMMERCE_KEY` / `WOOCOMMERCE_SECRET` | מ‑`.env` |
| `WP_USER` / `WP_APP_PASSWORD` | מ‑`.env` |

5. **Apply / Create** → Render יבנה ויפעיל. אחרי כ‑2–3 דקות תקבל **כתובת קבועה** כמו:
   `https://smadar-whatsapp.onrender.com`

---

## שלב 3 — לחבר את Meta לכתובת החדשה
1. ב‑Meta → האפליקציה → **WhatsApp → Configuration → Webhook → Edit**.
2. **Callback URL:** `https://smadar-whatsapp.onrender.com/webhook/whatsapp`
3. **Verify token:** `smdr_7fcdb62248421fc6533d1232`
4. **Verify and save** → ואז ודא שיש **Subscribe** ל‑`messages`.

---

## הוספת עוד טלפונים שלך
ב‑Meta → **WhatsApp → API Setup** → בשדה הנמענים (To) → **Add recipient** → הכנס כל מספר שלך (עד 5), ואשר את הקוד שמגיע בוואטסאפ. הוסף אותם גם ל‑`WHATSAPP_ALLOWED_NUMBERS` ב‑Render (מופרד בפסיק).

---

## ⚠️ הערה חשובה על התוכנית החינמית של Render
בתוכנית החינמית השרת "נרדם" אחרי ~15 דקות ללא שימוש. כשתשלח תמונה אחרי הפסקה, ההודעה הראשונה תתעכב ~30 שניות (Meta שולחת שוב אוטומטית, אז זה בדרך כלל עדיין עובד). אם תרצה שיהיה **מיידי תמיד** — יש שדרוג בתשלום (~7$ לחודש) ששומר אותו ער. לשימוש אישי מזדמן — החינמי מספיק.
