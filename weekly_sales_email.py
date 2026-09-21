# -*- coding: utf-8 -*-
"""
Weekly "new arrivals" sales email via Mailchimp.

Each week:
  1. collect products created on the store in the last 7 days (genuine new
     arrivals — newest first, capped),
  2. ensure a 10%-off coupon (code NEW10) restricted to exactly those products,
  3. build a Hebrew RTL HTML email featuring them (image, name, price, buy link)
     with the coupon,
  4. create a Mailchimp campaign to the store's audience and — with --send —
     send it. Without --send it stays a DRAFT for review.

Only sends when there is new stock, and only from SALES_EMAIL_SINCE onward
(so the initial bulk migration doesn't trigger a giant blast).

Env: MAILCHIMP_API_KEY, plus the usual WOOCOMMERCE_*/WP_*.
    python weekly_sales_email.py            # DRAFT (no send) — for review
    python weekly_sales_email.py --send      # create + SEND
"""
import datetime as dt
import html as _html
import os
import sys
import warnings

warnings.filterwarnings("ignore")
import requests
from dotenv import load_dotenv

load_dotenv()

from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient

SEND = "--send" in sys.argv
DAYS = 7
MAX_PRODUCTS = 16
COUPON_CODE = "NEW10"
COUPON_PCT = 10
SALES_EMAIL_SINCE = os.getenv("SALES_EMAIL_SINCE", "2026-09-27")   # don't blast during migration
SUBJECT = "חדש בגלריה לעיצוב הבית 🎁 ועוד 10% הנחה מיוחדת"
PREHEADER = "המוצרים החדשים שהגיעו השבוע — עם קוד להנחה נוספת של 10%"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _mc(dc, key):
    s = requests.Session()
    s.auth = ("anystring", key)
    s.headers.update({"User-Agent": UA, "Content-Type": "application/json"})
    s.base = f"https://{dc}.api.mailchimp.com/3.0"
    return s


def _money(v):
    try:
        return f"{int(round(float(v))):,} ₪"
    except (ValueError, TypeError):
        return ""


def fetch_new_products(c):
    since = (dt.datetime.utcnow() - dt.timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    prods = c.get("products", params={
        "after": since, "status": "publish", "orderby": "date", "order": "desc",
        "per_page": MAX_PRODUCTS, "stock_status": "instock",
    })
    out = []
    for p in prods:
        img = ""
        if p.get("images"):
            img = p["images"][0].get("src", "")
        price = p.get("price") or p.get("regular_price") or ""
        if not price:
            continue
        out.append({"id": p["id"], "name": p.get("name", ""), "price": price,
                    "url": p.get("permalink", ""), "image": img})
    return out


def ensure_coupon(c, product_ids):
    """Create or update the NEW10 coupon → 10% off, limited to these products."""
    existing = c.get("coupons", params={"code": COUPON_CODE})
    payload = {
        "code": COUPON_CODE, "discount_type": "percent", "amount": str(COUPON_PCT),
        "individual_use": False, "exclude_sale_items": False,
        "product_ids": product_ids,
        "description": "עוד 10% הנחה על המוצרים החדשים (מייל שבועי)",
    }
    if existing:
        c.put(f"coupons/{existing[0]['id']}", payload)
        return existing[0]["id"]
    res = c.post("coupons", payload)
    return res.get("id")


def build_html(products):
    cards = []
    for p in products:
        img = _html.escape(p["image"])
        name = _html.escape(p["name"])
        url = _html.escape(p["url"])
        price = _money(p["price"])
        cards.append(f"""
        <td valign="top" width="50%" style="padding:8px;">
          <table cellpadding="0" cellspacing="0" width="100%" style="border:1px solid #e7ddd9;border-radius:12px;overflow:hidden;background:#fffdfa;">
            <tr><td><a href="{url}"><img src="{img}" width="100%" style="display:block;max-width:100%;height:auto;" alt="{name}"></a></td></tr>
            <tr><td style="padding:12px 14px;font-family:Arial,sans-serif;">
              <div style="font-size:15px;font-weight:bold;color:#211b22;line-height:1.4;min-height:42px;">{name}</div>
              <div style="font-size:17px;color:#7a3d63;font-weight:bold;margin:8px 0;">{price}</div>
              <a href="{url}" style="display:inline-block;background:#7a3d63;color:#fff;text-decoration:none;font-family:Arial,sans-serif;font-size:14px;font-weight:bold;padding:9px 20px;border-radius:8px;">לצפייה ורכישה</a>
            </td></tr>
          </table>
        </td>""")
    # pair cards into rows of 2
    rows = []
    for i in range(0, len(cards), 2):
        rows.append("<tr>" + "".join(cards[i:i + 2]) + "</tr>")
    grid = "\n".join(rows)

    return f"""<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#f4f0eb;">
<span style="display:none;visibility:hidden;opacity:0;height:0;width:0;">{_html.escape(PREHEADER)}</span>
<table cellpadding="0" cellspacing="0" width="100%" style="background:#f4f0eb;padding:0;">
 <tr><td align="center" style="padding:22px 12px;">
  <table cellpadding="0" cellspacing="0" width="600" style="max-width:600px;width:100%;">
   <tr><td align="center" style="font-family:Arial,sans-serif;padding:6px 0 4px;">
     <div style="font-size:13px;letter-spacing:2px;color:#7a3d63;font-weight:bold;">הגלריה לעיצוב הבית · סמדר בטיטו</div>
     <div style="font-size:26px;font-weight:bold;color:#211b22;margin:6px 0;">חדש אצלנו השבוע ✨</div>
     <div style="font-size:15px;color:#6b6169;">בחרנו בשבילכם את המוצרים החדשים שהגיעו — ובלעדית לכם, עוד הנחה.</div>
   </td></tr>
   <tr><td style="padding:14px 6px;">
     <table cellpadding="0" cellspacing="0" width="100%" style="background:#7a3d63;border-radius:12px;">
       <tr><td align="center" style="font-family:Arial,sans-serif;padding:16px;color:#fff;">
         <div style="font-size:15px;">קוד קופון ל־10% הנחה נוספת על המוצרים החדשים:</div>
         <div style="font-size:26px;font-weight:bold;letter-spacing:3px;margin:8px 0;background:#fff;color:#7a3d63;display:inline-block;padding:8px 24px;border-radius:8px;">{COUPON_CODE}</div>
         <div style="font-size:12px;opacity:.85;">הזינו את הקוד בעגלה. בתוקף לזמן מוגבל.</div>
       </td></tr>
     </table>
   </td></tr>
   <tr><td><table cellpadding="0" cellspacing="0" width="100%">{grid}</table></td></tr>
   <tr><td align="center" style="font-family:Arial,sans-serif;padding:20px 12px;color:#6b6169;font-size:13px;line-height:1.7;">
     החנות הפיזית: כוכב הצפון 8, אשדוד · וואטסאפ/טלפון: 050-5766659<br>
     משלוחים לכל הארץ · <a href="https://smadarbetitohome.co.il" style="color:#7a3d63;">לאתר המלא</a>
     <div style="margin-top:12px;font-size:11px;color:#9a9098;">*|UNSUB|* מהדיוור</div>
   </td></tr>
  </table>
 </td></tr>
</table></body></html>"""


def main():
    today = dt.date.today().isoformat()
    if SALES_EMAIL_SINCE and today < SALES_EMAIL_SINCE:
        print(f"Before SALES_EMAIL_SINCE ({SALES_EMAIL_SINCE}) — skipping (migration window).", flush=True)
        return

    key = os.getenv("MAILCHIMP_API_KEY", "").strip()
    if not key or "-" not in key:
        print("MAILCHIMP_API_KEY missing/invalid.", flush=True)
        sys.exit(1)
    dc = key.rsplit("-", 1)[1]
    mc = _mc(dc, key)

    # pick the audience (first list; or MAILCHIMP_LIST_ID override)
    list_id = os.getenv("MAILCHIMP_LIST_ID", "").strip()
    if not list_id:
        r = mc.get(f"{mc.base}/lists?count=5&fields=lists.id,lists.campaign_defaults", timeout=40)
        lists = r.json().get("lists", [])
        if not lists:
            print("No Mailchimp audience found.", flush=True); sys.exit(1)
        list_id = lists[0]["id"]
    defaults = mc.get(f"{mc.base}/lists/{list_id}?fields=campaign_defaults", timeout=40).json().get("campaign_defaults", {})

    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=False)

    products = fetch_new_products(c)
    print(f"new products (last {DAYS}d): {len(products)}", flush=True)
    if not products:
        print("No new products this week — no email sent.", flush=True)
        return

    coupon_id = ensure_coupon(c, [p["id"] for p in products])
    print(f"coupon {COUPON_CODE} ready (id {coupon_id}) on {len(products)} products", flush=True)

    html_body = build_html(products)

    # create campaign
    camp = mc.post(f"{mc.base}/campaigns", json={
        "type": "regular",
        "recipients": {"list_id": list_id},
        "settings": {
            "subject_line": SUBJECT,
            "preview_text": PREHEADER,
            "title": f"New arrivals {today}",
            "from_name": defaults.get("from_name") or "SMADAR BETITO HOME DESIGN",
            "reply_to": defaults.get("from_email") or "yagelbbb@gmail.com",
            "to_name": "*|FNAME|*",
            "auto_footer": False,
        },
    }, timeout=40).json()
    cid = camp.get("id")
    if not cid:
        print(f"campaign create failed: {str(camp)[:300]}", flush=True); sys.exit(1)
    mc.put(f"{mc.base}/campaigns/{cid}/content", json={"html": html_body}, timeout=40)
    print(f"campaign created: {cid}", flush=True)

    if SEND:
        r = mc.post(f"{mc.base}/campaigns/{cid}/actions/send", timeout=40)
        if r.status_code in (200, 204):
            print(f"✅ SENT campaign {cid} to audience {list_id}", flush=True)
        else:
            print(f"send failed {r.status_code}: {r.text[:300]}", flush=True); sys.exit(1)
    else:
        print(f"DRAFT ready (not sent). Review it in Mailchimp → Campaigns → 'New arrivals {today}'.", flush=True)


if __name__ == "__main__":
    main()
