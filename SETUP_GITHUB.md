# הרצת הסנכרון בענן (GitHub Actions) — בלי שהמחשב יהיה דלוק

הסנכרון יכול לרוץ **בשרתים של GitHub פעם בשבוע**, כך שהמחשב שלך לא צריך להיות מחובר.

## מה כבר מוכן בקוד
- `.github/workflows/weekly-sync.yml` — סנכרון **שבועי** (יום ראשון 01:00 UTC) + הרצה ידנית מהכפתור "Run workflow". מריץ את `run_and_verify.py` (כל התיקונים).
- `.github/workflows/ci.yml` — בדיקת קוד (ruff lint + pytest) בכל push.
- הקוד כבר עבר commit מקומי. נשאר רק להעלות ל‑GitHub ולהגדיר סודות.

---

## שלב 1 — צור מאגר (repo) ב‑GitHub
1. היכנס ל‑https://github.com/new
2. שם: `supplier-sync` · בחר **Private** · אל תסמן "Add README".
3. Create repository.

## שלב 2 — העלה את הקוד (push)
בטרמינל, בתיקיית הפרויקט:
```
git remote add origin https://github.com/<USERNAME>/supplier-sync.git
git branch -M main
git push -u origin main
```
תתבקש שם משתמש + סיסמה. ב‑GitHub הסיסמה היא **Personal Access Token** (לא סיסמת החשבון):
צור אחד ב‑https://github.com/settings/tokens → "Generate new token (classic)" → סמן `repo` → העתק והשתמש בו כסיסמה.

## שלב 3 — הוסף את הסודות (Secrets)
ב‑repo: **Settings → Secrets and variables → Actions → New repository secret**.
הוסף את התשעה הבאים (הערכים נמצאים בקובץ `.env` המקומי שלך):

| שם הסוד (בדיוק כך) | מאיפה הערך |
|---|---|
| `WOOCOMMERCE_URL` | `https://smadarbetitohome.co.il` |
| `WOOCOMMERCE_KEY` | מ‑`.env` (WOOCOMMERCE_KEY) |
| `WOOCOMMERCE_SECRET` | מ‑`.env` |
| `WP_USER` | `Yagelbbb@gmail.com` |
| `WP_APP_PASSWORD` | מ‑`.env` (ה‑Application Password) |
| `OPENAI_API_KEY` | מ‑`.env` |
| `PALDINOX_USER` | `1821` |
| `PALDINOX_PASSWORD` | מ‑`.env` |
| `PALDINOX_DOC_NUM` | `293857` |

## שלב 4 — בדיקה
ב‑repo: **Actions → Weekly Supplier Sync → Run workflow** (אפשר עם dry_run=true לבדיקה ראשונה).
מאז — זה ירוץ אוטומטית כל שבוע, בלי המחשב.

---

## אופציה מהירה יותר (שאני אעשה את הכל)
התקן GitHub CLI והתחבר:
```
winget install GitHub.cli
gh auth login
```
ואז תגיד לי — אני אריץ עבורך את יצירת ה‑repo, ה‑push, והוספת כל הסודות בפקודה אחת.
