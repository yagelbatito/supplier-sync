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
import re
import sys
import warnings

warnings.filterwarnings("ignore")
import requests
from dotenv import load_dotenv

load_dotenv()

from src.core.config_loader import load_app_settings
from src.woocommerce.client import WooCommerceClient

SEND = "--send" in sys.argv
NEW_COLLECTION_NAME = "New collection"
NEW_COLLECTION_COUNT = 5            # newest 5 from the New collection
# One most-recently-updated product from each of these categories:
HIGHLIGHT_CATEGORIES = [
    "כיסאות בר", "כורסאות", "כיסאות אוכל", "הדומים", "שולחנות סלון", "שולחנות אוכל",
]
COUPON_CODE = "10"
COUPON_PCT = 10
SALES_EMAIL_SINCE = os.getenv("SALES_EMAIL_SINCE", "")   # gate disabled — curated 11-product email, safe to send weekly
SUBJECT = "חדש בגלריה לעיצוב הבית — ועוד 10% הנחה מיוחדת"
PREHEADER = "המוצרים החדשים שהגיעו השבוע — עם קוד להנחה נוספת של 10%"

# Store / brand details for the email (matches the owner's preferred design).
LOGO_URL = "https://mcusercontent.com/a28620ed60ea09878bfbe5e5b/images/6d151d1e-a1f0-5508-2201-c4fc81f4315b.png"
STORE_PHONE = "053-3221955"
STORE_ADDR = "כוכב הצפון 8, אשדוד"

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


def _category_id(c, name):
    try:
        for cat in c.get("products/categories", params={"search": name, "per_page": 20}):
            if cat.get("name") == name:
                return cat["id"]
    except Exception:
        pass
    return None


def _card(p):
    """WC product dict → email card dict, or None if not showable."""
    img = p["images"][0].get("src", "") if p.get("images") else ""
    price = p.get("price") or p.get("regular_price") or ""
    if not img or not price:
        return None
    desc = re.sub(r"<[^>]+>", " ", p.get("short_description") or "")
    desc = re.sub(r"\s+", " ", desc).strip()
    if len(desc) > 130:
        desc = desc[:127].rstrip() + "…"
    return {"id": p["id"], "name": p.get("name", ""), "price": price,
            "url": p.get("permalink", ""), "image": img, "desc": desc}


def _fetch_cat(c, cat_id, orderby, count):
    return c.get("products", params={
        "category": cat_id, "status": "publish", "stock_status": "instock",
        "orderby": orderby, "order": "desc", "per_page": max(count + 4, 6),
    })


def select_products(c):
    """5 newest from 'New collection' + 1 most-recently-updated from each
    highlight category. De-duplicated, in that order."""
    picks, seen = [], set()

    def take(products, limit):
        n = 0
        for p in products:
            if p["id"] in seen:
                continue
            card = _card(p)
            if not card:
                continue
            seen.add(p["id"]); picks.append(card); n += 1
            if n >= limit:
                break

    nc_id = _category_id(c, NEW_COLLECTION_NAME)
    if nc_id:
        take(_fetch_cat(c, nc_id, "date", NEW_COLLECTION_COUNT), NEW_COLLECTION_COUNT)
    for name in HIGHLIGHT_CATEGORIES:
        cid = _category_id(c, name)
        if cid:
            take(_fetch_cat(c, cid, "modified", 1), 1)
    return picks


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
    """Editorial layout the owner preferred: cream ground, dark header + footer,
    gold accents, serif headings; single-column full-width product cards."""
    cards = []
    for p in products:
        img = _html.escape(p["image"])
        name = _html.escape(p["name"])
        url = _html.escape(p["url"])
        price = _money(p["price"])
        desc = _html.escape(p.get("desc") or "")
        desc_row = (f'<tr><td align="right" style="font-family:Arial,\'Helvetica Neue\',sans-serif;'
                    f'font-size:16px;line-height:25px;color:#5F5A50;padding:0 0 16px 0;">{desc}</td></tr>') if desc else ""
        cards.append(f"""
    <tr><td style="padding:0 18px 24px 18px;">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#FFFDF9;border:1px solid #E1D6C2;border-collapse:separate;border-radius:8px;overflow:hidden;">
        <tr><td>
          <a href="{url}" target="_blank" style="text-decoration:none;">
            <img src="{img}" width="562" alt="{name}" style="display:block;width:100%;max-width:562px;height:auto;border:0;outline:none;text-decoration:none;">
          </a>
        </td></tr>
        <tr><td align="right" dir="rtl" style="padding:22px 24px 24px 24px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
            <tr><td align="right" style="font-family:Arial,'Helvetica Neue',sans-serif;font-size:24px;line-height:32px;font-weight:bold;color:#3E3A33;padding:0 0 8px 0;">{name}</td></tr>
            {desc_row}
            <tr><td align="right" style="font-family:Georgia,'Times New Roman',serif;font-size:30px;line-height:36px;font-weight:bold;color:#A9812F;padding:0 0 18px 0;">{price}</td></tr>
            <tr><td align="right">
              <table role="presentation" cellspacing="0" cellpadding="0" border="0" align="right"><tr><td bgcolor="#3E3A33" style="border-radius:4px;">
                <a href="{url}" target="_blank" style="display:inline-block;font-family:Arial,'Helvetica Neue',sans-serif;font-size:16px;line-height:20px;font-weight:bold;color:#FFFDF9;text-decoration:none;padding:13px 24px;border:1px solid #3E3A33;border-radius:4px;">לצפייה במוצר ←</a>
              </td></tr></table>
            </td></tr>
          </table>
        </td></tr>
      </table>
    </td></tr>""")
    grid = "\n".join(cards)

    return f"""<!doctype html><html lang="he" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<style type="text/css">
  body{{margin:0!important;padding:0!important;background:#F6F3EC!important;}}
  table{{border-spacing:0;}} img{{border:0;}}
  @media only screen and (max-width:620px){{
    .email-container{{width:100%!important;max-width:100%!important;}}
    .mobile-pad{{padding-left:16px!important;padding-right:16px!important;}}
    .hero-title{{font-size:32px!important;line-height:40px!important;}}
  }}
</style></head>
<body dir="rtl" style="margin:0;padding:0;background:#F6F3EC;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{_html.escape(PREHEADER)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#F6F3EC">
<tr><td align="center">
<table role="presentation" width="600" class="email-container" cellspacing="0" cellpadding="0" border="0" style="width:600px;max-width:600px;margin:0 auto;background:#F6F3EC;">

  <tr><td align="center" bgcolor="#3E3A33" style="padding:12px 20px;font-family:Arial,sans-serif;font-size:14px;line-height:20px;color:#EAD9B8;font-weight:bold;">✨ חדש במלאי · אספקה מיידית</td></tr>

  <tr><td align="center" bgcolor="#3E3A33" class="mobile-pad" style="padding:38px 28px 44px 28px;">
    <img src="{LOGO_URL}" width="110" alt="סמדר בטיטו" style="display:block;width:110px;max-width:110px;height:auto;margin:0 auto 20px auto;">
    <div class="hero-title" dir="rtl" style="font-family:Georgia,'Times New Roman',serif;font-size:42px;line-height:50px;font-weight:bold;color:#FFFDF9;text-align:center;">חדש אצלנו השבוע</div>
    <div dir="rtl" style="font-family:Arial,sans-serif;font-size:18px;line-height:28px;color:#D8CCBB;text-align:center;padding-top:14px;">בחרנו עבורכם את המוצרים החדשים שהגיעו — ובלעדית לכם, עוד הנחה.</div>
  </td></tr>

  <tr><td style="padding:24px 18px 8px 18px;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#FFFDF9;border:2px solid #A9812F;border-radius:8px;">
      <tr><td align="center" dir="rtl" style="padding:20px 18px;font-family:Arial,sans-serif;">
        <div style="font-size:15px;color:#5F5A50;">קוד קופון ל־10% הנחה נוספת על המוצרים החדשים:</div>
        <div style="font-family:Georgia,'Times New Roman',serif;font-size:30px;font-weight:bold;letter-spacing:4px;color:#A9812F;margin:8px 0;">{COUPON_CODE}</div>
        <div style="font-size:12px;color:#8A7F72;">הזינו את הקוד בעגלה · בתוקף לזמן מוגבל, בכפוף למלאי</div>
      </td></tr>
    </table>
  </td></tr>

  <tr><td align="center" style="padding:26px 20px 20px 20px;">
    <div dir="rtl" style="font-family:Arial,sans-serif;font-size:13px;line-height:20px;color:#767E5E;font-weight:bold;letter-spacing:1px;">חדש בקולקציה</div>
    <div dir="rtl" style="font-family:Georgia,'Times New Roman',serif;font-size:30px;line-height:38px;color:#3E3A33;font-weight:bold;padding-top:6px;">הפריטים שבחרנו במיוחד עבורכם</div>
  </td></tr>
{grid}
  <tr><td align="center" bgcolor="#767E5E" dir="rtl" style="padding:38px 24px;font-family:Arial,sans-serif;color:#FFFDF9;">
    <div style="font-size:27px;line-height:35px;font-weight:bold;padding-bottom:10px;">אל תפספסו — המלאי מתחדש כל שבוע</div>
    <div style="font-size:16px;line-height:25px;">נשמח לעזור לכם לבחור את הפריט המתאים לבית.</div>
  </td></tr>

  <tr><td align="center" bgcolor="#3E3A33" dir="rtl" style="padding:34px 24px;color:#D8CCBB;font-family:Arial,sans-serif;">
    <img src="{LOGO_URL}" width="82" alt="סמדר בטיטו" style="display:block;width:82px;max-width:82px;height:auto;margin:0 auto 14px auto;">
    <div style="font-family:Georgia,'Times New Roman',serif;font-size:24px;line-height:30px;font-weight:bold;color:#E8C97A;">הגלריה לעיצוב הבית — סמדר בטיטו</div>
    <div style="font-size:15px;line-height:24px;padding-top:10px;">{STORE_ADDR} &nbsp;|&nbsp; {STORE_PHONE}</div>
    <div style="font-size:13px;line-height:20px;color:#AFA38F;padding-top:18px;">המחירים והמבצעים כפופים למלאי הקיים ולתנאי החנות.<br><a href="*|UNSUB|*" style="color:#AFA38F;">להסרה מרשימת התפוצה</a></div>
  </td></tr>

</table>
</td></tr></table></body></html>"""


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

    products = select_products(c)
    print(f"selected products: {len(products)} "
          f"({NEW_COLLECTION_COUNT} newest from New collection + highlights)", flush=True)
    if not products:
        print("No products to feature — no email sent.", flush=True)
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
