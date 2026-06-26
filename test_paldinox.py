"""
בדיקה מהירה שה-token של פלדינוקס תקין לפני הרצת הסנכרון.
הרץ: python test_paldinox.py
"""
import os, warnings, datetime
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
import requests

load_dotenv()
token = os.getenv("PALDINOX_TOKEN", "").strip()
doc_num = os.getenv("PALDINOX_DOC_NUM", "").strip()

if not token:
    print("❌ PALDINOX_TOKEN לא מוגדר ב-.env")
    exit(1)

# בדוק תאריך פקיעה מתוך ה-token
try:
    import base64, json
    payload = token.split(".")[1]
    payload += "=" * (4 - len(payload) % 4)
    decoded = json.loads(base64.b64decode(payload))
    exp = decoded.get("exp", 0)
    exp_dt = datetime.datetime.fromtimestamp(exp)
    now = datetime.datetime.now()
    diff = exp_dt - now
    if diff.total_seconds() < 0:
        print(f"❌ Token פג לפני {abs(int(diff.total_seconds()/60))} דקות!")
        print(f"   פג ב: {exp_dt.strftime('%H:%M:%S')}")
        print("   צריך token חדש מהדפדפן")
        exit(1)
    else:
        mins = int(diff.total_seconds() / 60)
        print(f"✅ Token בתוקף עוד {mins} דקות (עד {exp_dt.strftime('%H:%M:%S')})")
except Exception:
    print("⚠️  לא ניתן לפענח תאריך פקיעה, בודק API...")

# בדוק API עם בקשה קטנה
print("🔌 בודק חיבור ל-API...")
try:
    resp = requests.post(
        "https://b2b.paldinox.co.il/AmPortal/api/Sales/getProductsForClient",
        json={
            "amodatDocTypeID": 13, "departmentIDs": [], "docNum": doc_num,
            "isDefaultsApplied": True, "page": 1, "pageSize": 1,
            "productID": "", "productName": "",
            "showOnlyClientProducts": False, "showOnlyDocProducts": False,
            "showOnlyFavoritesProducts": False, "sortColumn": "", "sortOrder": "",
            "userParams": {"webViewSettings": {"screens": [{"screenID": "HomeB2BClient", "screenMode": "*"}]}}
        },
        headers={
            "Content-Type": "application/json",
            "Cookie": f"token={token}",
            "User-Agent": "Mozilla/5.0",
        },
        verify=False,
        timeout=10,
    )
    if resp.status_code == 401:
        print("❌ Token לא תקין — 401 Unauthorized")
        print("   היכנסי לפלדינוקס בדפדפן והעתיקי token חדש")
        exit(1)
    elif resp.status_code == 200:
        data = resp.json()
        if data.get("status"):
            total = len(data.get("data", []))
            print(f"✅ API עובד! קיבלתי {total} מוצרים בבדיקה")
            print("🚀 אפשר להריץ: python -m src.main --supplier paldinox")
        else:
            print(f"⚠️  API החזיר שגיאה: {data.get('message')}")
    else:
        print(f"❌ שגיאה {resp.status_code}: {resp.text[:200]}")
except Exception as e:
    print(f"❌ שגיאת חיבור: {e}")
