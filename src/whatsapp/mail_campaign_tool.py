# -*- coding: utf-8 -*-
"""Ad-hoc promo email builder for the admin panel (served by the bot's FastAPI).

The owner opens /mail, types an occasion + free text, pastes a few PRODUCT links,
and the tool:
  1. resolves the links to real products,
  2. creates a 10% coupon valid for 7 days, restricted to exactly those products,
  3. builds an editorial email (same look as the weekly one) with the chosen
     products + the coupon code,
  4. creates it as a Mailchimp DRAFT, then offers "send test to me" / "send to all".

Routes (gated by ?key= like the other admin tools):
    GET  /mail               → the HTML page
    POST /api/mail/create    → {title,text,coupon_code,percent,days,urls[]} → draft
    POST /api/mail/test      → {campaign_id, email} → send a test
    POST /api/mail/send      → {campaign_id} → send to the whole audience
"""
import datetime as dt
import html as _html
import os
import re

import requests
from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger

logger = get_logger(__name__)


def _key() -> str:
    return (os.getenv("BOT_ADMIN_KEY") or os.getenv("CATEGORY_TOOL_KEY")
            or os.getenv("WHATSAPP_VERIFY_TOKEN", "")).strip()


def _check(key: str) -> None:
    if not _key() or key != _key():
        raise HTTPException(status_code=403, detail="unauthorized")


def _mc():
    key = os.getenv("MAILCHIMP_API_KEY", "").strip()
    if not key or "-" not in key:
        raise HTTPException(status_code=400, detail="MAILCHIMP_API_KEY missing")
    dc = key.rsplit("-", 1)[1]
    s = requests.Session()
    s.auth = ("anystring", key)
    s.base = f"https://{dc}.api.mailchimp.com/3.0"
    return s


def _slug(url: str) -> str:
    m = re.search(r"/product/([^/?#]+)", url.strip())
    return m.group(1) if m else ""


def _money(v):
    try:
        return f"{float(v):,.0f} ₪"
    except (TypeError, ValueError):
        return ""


def _resolve(wc, urls):
    """Product URLs → [{id,name,price,url,image}] (keeps the given order)."""
    out = []
    for u in urls:
        slug = _slug(u)
        if not slug:
            continue
        try:
            r = wc.get("products", params={"slug": slug, "_fields": "id,name,price,permalink,images"})
        except Exception:
            r = None
        if r:
            p = r[0]
            out.append({"id": p["id"], "name": p.get("name", ""),
                        "price": p.get("price"), "url": p.get("permalink") or u,
                        "image": (p.get("images") or [{}])[0].get("src", "")})
    return out


def _coupon(wc, code, pct, days, product_ids):
    exp = (dt.date.today() + dt.timedelta(days=days)).isoformat()
    payload = {"code": code, "discount_type": "percent", "amount": str(pct),
               "individual_use": False, "product_ids": product_ids,
               "date_expires": exp,
               "description": f"מבצע — {pct}% הנחה על מוצרים נבחרים (בתוקף עד {exp})"}
    existing = wc.get("coupons", params={"code": code})
    if existing:
        wc.put(f"coupons/{existing[0]['id']}", payload)
        return existing[0]["id"], exp
    return wc.post("coupons", payload).get("id"), exp


def _build_html(title, text, products, code, exp):
    cards = []
    for p in products:
        cards.append(f"""
      <tr><td style="padding:0 18px 24px 18px;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#FFFDF9;border:1px solid #E1D6C2;border-radius:8px;overflow:hidden;">
          <tr><td><a href="{_html.escape(p['url'])}" target="_blank"><img src="{_html.escape(p['image'])}" width="562" alt="{_html.escape(p['name'])}" style="display:block;width:100%;max-width:562px;height:auto;border:0;"></a></td></tr>
          <tr><td align="right" dir="rtl" style="padding:20px 24px;">
            <div style="font-family:Arial,sans-serif;font-size:22px;font-weight:bold;color:#3E3A33;">{_html.escape(p['name'])}</div>
            <div style="font-family:Arial,sans-serif;font-size:18px;color:#A9812F;padding-top:6px;">{_money(p['price'])}</div>
            <a href="{_html.escape(p['url'])}" target="_blank" style="display:inline-block;margin-top:12px;background:#3E3A33;color:#fff;text-decoration:none;font-family:Arial,sans-serif;font-size:15px;padding:10px 22px;border-radius:6px;">לצפייה במוצר ›</a>
          </td></tr>
        </table>
      </td></tr>""")
    text_html = _html.escape(text).replace("\n", "<br>")
    return f"""<!doctype html><html dir="rtl" lang="he"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#F6F3EC;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#F6F3EC;"><tr><td align="center" style="padding:24px 8px;">
<table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" style="width:600px;max-width:600px;">
  <tr><td align="center" style="background:#3E3A33;color:#F6F3EC;font-family:Georgia,serif;font-size:30px;padding:28px 20px;">{_html.escape(title)}</td></tr>
  <tr><td align="right" dir="rtl" style="font-family:Arial,sans-serif;font-size:17px;line-height:27px;color:#5F5A50;padding:24px 24px 8px 24px;">{text_html}</td></tr>
  <tr><td align="center" style="padding:16px 18px 24px 18px;">
     <table role="presentation" width="100%" style="background:#A9812F;border-radius:8px;"><tr><td align="center" dir="rtl" style="padding:18px;font-family:Arial,sans-serif;color:#fff;">
       <div style="font-size:16px;">קוד קופון — 10% הנחה על המוצרים במבצע</div>
       <div style="font-size:30px;font-weight:bold;letter-spacing:3px;padding:6px 0;">{_html.escape(code)}</div>
       <div style="font-size:13px;opacity:.9;">בתוקף עד {exp}</div>
     </td></tr></table>
  </td></tr>
  {''.join(cards)}
  <tr><td align="center" style="background:#3E3A33;color:#CFC7B8;font-family:Arial,sans-serif;font-size:13px;padding:22px;">סמדר בטיטו — עיצוב הבית · 053-3221955</td></tr>
</table></td></tr></table></body></html>"""


def _story_html(title, code, exp, img_url, pct, key):
    """A 1080×1920 Instagram-story card. Rendered once by html2canvas at full
    size (NO CSS transform on the captured node — that caused doubled text), then
    shown as a plain <img> the user downloads."""
    proxied = f"/api/mail/img?key={_html.escape(key)}&url={_html.escape(img_url)}" if img_url else ""
    return f"""<!doctype html><html dir=rtl lang=he><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>סטורי מבצע</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<style>
html,body{{margin:0;background:#2b2823;font-family:Arial,sans-serif;text-align:center}}
#stage{{position:absolute;top:0;left:0;z-index:-1}}
#story{{width:1080px;height:1920px;background:#F6F3EC;overflow:hidden;box-sizing:border-box;padding:80px 70px;text-align:center;position:relative}}
.occ{{font-family:Georgia,serif;font-size:100px;color:#3E3A33;font-weight:bold;line-height:1.1;margin:16px 0 4px}}
.sub{{font-size:42px;color:#5F5A50;margin-bottom:34px}}
.imgbox{{width:940px;height:900px;margin:0 auto;border-radius:28px;overflow:hidden;background:#e9e3d6}}
.imgbox img{{width:100%;height:100%;object-fit:cover;display:block}}
.coupon{{margin:54px auto 0;background:#A9812F;color:#fff;border-radius:24px;padding:44px 30px;width:820px}}
.coupon .p{{font-size:50px;font-weight:bold}}
.coupon .code{{font-size:104px;font-weight:bold;letter-spacing:6px;margin:12px 0}}
.coupon .t{{font-size:40px;opacity:.92}}
.foot{{position:absolute;bottom:66px;left:0;right:0;font-size:40px;color:#3E3A33}}
#out{{max-width:100%;max-height:96vh;display:none}}
#dl{{position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:9;background:#A9812F;color:#fff;border:0;padding:16px 30px;border-radius:10px;font-size:18px;font-weight:bold;cursor:pointer;display:none}}
#hint{{color:#EFE7D8;padding:40px;font-size:18px}}
</style></head><body>
<button id=dl>⬇️ הורד תמונת סטורי</button>
<div id=hint>מכין את הסטורי…</div>
<img id=out alt="story">
<div id=stage><div id=story>
  <div class=occ>{_html.escape(title or 'מבצע מיוחד')}</div>
  <div class=sub>לזמן מוגבל בלבד</div>
  <div class=imgbox>{f'<img crossorigin=anonymous src="{proxied}">' if proxied else ''}</div>
  <div class=coupon>
    <div class=p>{_html.escape(str(pct))}% הנחה · קוד קופון</div>
    <div class=code>{_html.escape(code or '')}</div>
    <div class=t>בתוקף עד {_html.escape(exp or '')}</div>
  </div>
  <div class=foot>סמדר בטיטו · עיצוב הבית · 053-3221955</div>
</div></div>
<script>
function gen(){{
 html2canvas(document.getElementById('story'),{{scale:1,useCORS:true,backgroundColor:'#F6F3EC'}}).then(function(c){{
   var url=c.toDataURL('image/png');
   var out=document.getElementById('out'); out.src=url; out.style.display='inline-block';
   var st=document.getElementById('stage'); if(st) st.remove();
   document.getElementById('hint').style.display='none';
   var dl=document.getElementById('dl'); dl.style.display='block';
   dl.onclick=function(){{var a=document.createElement('a');a.download='story.png';a.href=url;a.click();}};
 }});
}}
window.onload=function(){{
 var im=document.querySelector('#story img');
 if(im && !im.complete){{im.onload=gen; im.onerror=gen; setTimeout(gen,4000);}} else {{setTimeout(gen,200);}}
}};
</script></body></html>"""


_PAGE = """<!doctype html><html dir=rtl lang=he><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>יצירת מייל דיוור</title>
<style>body{font-family:Arial;background:#F6F3EC;margin:0;padding:20px;color:#3E3A33}
.box{max-width:680px;margin:auto;background:#fff;border:1px solid #E1D6C2;border-radius:10px;padding:22px}
h1{font-family:Georgia,serif} label{display:block;margin:14px 0 4px;font-weight:bold}
input,textarea{width:100%;box-sizing:border-box;padding:10px;border:1px solid #ccc;border-radius:6px;font-size:15px}
textarea{min-height:90px} button{background:#3E3A33;color:#fff;border:0;padding:12px 22px;border-radius:6px;font-size:15px;cursor:pointer;margin-top:14px}
button.gold{background:#A9812F}.msg{margin-top:14px;padding:10px;border-radius:6px;background:#f4efe4;display:none}
iframe{width:100%;height:520px;border:1px solid #ddd;border-radius:8px;margin-top:14px;display:none}
.row{display:flex;gap:10px;flex-wrap:wrap}.row>*{flex:1}</style>
<div class=box>
<h1>יצירת מייל דיוור — מבצע</h1>
<label>כותרת / אירוע</label><input id=title placeholder="מבצע חג סוכות 🍋">
<label>מלל (טקסט חופשי לגוף המייל)</label><textarea id=text placeholder="לכבוד החג בחרנו עבורכם..."></textarea>
<div class=row><div><label>קוד קופון</label><input id=code placeholder="SUKKOT10"></div>
<div><label>אחוז הנחה</label><input id=pct type=number value=10></div>
<div><label>תוקף (ימים)</label><input id=days type=number value=7></div></div>
<label>קישורי מוצרים (אחד בכל שורה)</label><textarea id=urls placeholder="https://smadarbetitohome.co.il/product/..."></textarea>
<button onclick=createC()>צור מבצע (טיוטה)</button>
<div class=msg id=msg></div>
<iframe id=prev></iframe>
<div id=actions style=display:none>
<button onclick=storyC()>🖼️ צור סטורי לאינסטגרם</button>
<label>מייל לבדיקה</label><input id=testmail placeholder="your@email.com">
<button onclick=testC()>שלח מייל ניסיון אליי</button>
<button class=gold onclick=sendC()>שלח לכולם ✉️</button>
</div>
</div>
<script>
const KEY=new URLSearchParams(location.search).get('key')||'';let CID='';
const msg=(t)=>{let m=document.getElementById('msg');m.style.display='block';m.textContent=t;};
async function createC(){msg('יוצר...');
 let urls=document.getElementById('urls').value.split('\\n').map(s=>s.trim()).filter(Boolean);
 let r=await fetch('/api/mail/create?key='+KEY,{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({title:document.getElementById('title').value,text:document.getElementById('text').value,
   coupon_code:document.getElementById('code').value,percent:+document.getElementById('pct').value,
   days:+document.getElementById('days').value,urls})});
 let d=await r.json(); if(!r.ok){msg('שגיאה: '+(d.detail||'')); return;}
 window.D=d; CID=d.campaign_id; msg('נוצר! '+d.product_count+' מוצרים, קופון '+d.coupon_code+' (תוקף עד '+d.expires+'). בדוק בתצוגה למטה.');
 let f=document.getElementById('prev'); f.style.display='block'; f.srcdoc=d.html;
 document.getElementById('actions').style.display='block';}
function storyC(){let d=window.D||{};
 let u='/story?key='+encodeURIComponent(KEY)+'&t='+encodeURIComponent(d.title||'')+'&c='+encodeURIComponent(d.coupon_code||'')
   +'&e='+encodeURIComponent(d.expires||'')+'&pct='+encodeURIComponent(d.percent||10)+'&img='+encodeURIComponent(d.image||'');
 window.open(u,'_blank');}
async function testC(){msg('שולח ניסיון...');
 let r=await fetch('/api/mail/test?key='+KEY,{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({campaign_id:CID,email:document.getElementById('testmail').value})});
 let d=await r.json(); msg(r.ok?'מייל ניסיון נשלח ✓':'שגיאה: '+(d.detail||''));}
async function sendC(){if(!confirm('לשלוח לכל רשימת התפוצה?'))return; msg('שולח לכולם...');
 let r=await fetch('/api/mail/send?key='+KEY,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({campaign_id:CID})});
 let d=await r.json(); msg(r.ok?'נשלח לכולם ✓':'שגיאה: '+(d.detail||''));}
</script>"""


def register_mail_campaign_tool(app, orchestrator) -> None:
    wc = orchestrator.wc_client

    @app.get("/mail", response_class=HTMLResponse)
    def mail_page(key: str = ""):
        _check(key)
        return HTMLResponse(_PAGE)

    @app.post("/api/mail/create")
    async def mail_create(body: dict, key: str = ""):
        _check(key)
        title = str(body.get("title") or "מבצע").strip()
        text = str(body.get("text") or "").strip()
        pct = int(body.get("percent") or 10)
        days = int(body.get("days") or 7)
        code = (str(body.get("coupon_code") or "").strip()
                or "PROMO" + dt.date.today().strftime("%m%d"))
        urls = [u for u in (body.get("urls") or []) if u]
        products = _resolve(wc, urls)
        if not products:
            raise HTTPException(status_code=400, detail="no products resolved from links")
        _cid, exp = _coupon(wc, code, pct, days, [p["id"] for p in products])
        html_body = _build_html(title, text, products, code, exp)

        mc = _mc()
        list_id = os.getenv("MAILCHIMP_LIST_ID", "").strip()
        if not list_id:
            lists = mc.get(f"{mc.base}/lists?count=1&fields=lists.id", timeout=40).json().get("lists", [])
            if not lists:
                raise HTTPException(status_code=400, detail="no Mailchimp audience")
            list_id = lists[0]["id"]
        defaults = mc.get(f"{mc.base}/lists/{list_id}?fields=campaign_defaults", timeout=40).json().get("campaign_defaults", {})
        camp = mc.post(f"{mc.base}/campaigns", json={
            "type": "regular", "recipients": {"list_id": list_id},
            "settings": {"subject_line": title, "preview_text": text[:120],
                         "title": f"promo {code}",
                         "from_name": defaults.get("from_name") or "SMADAR BETITO HOME DESIGN",
                         "reply_to": defaults.get("from_email") or "yagelbbb@gmail.com",
                         "to_name": "*|FNAME|*", "auto_footer": False}}, timeout=40).json()
        cid = camp.get("id")
        if not cid:
            raise HTTPException(status_code=400, detail=f"campaign create failed: {str(camp)[:200]}")
        mc.put(f"{mc.base}/campaigns/{cid}/content", json={"html": html_body}, timeout=40)
        logger.info(f"[mail] draft {cid} — {len(products)} products, coupon {code}")
        return {"campaign_id": cid, "coupon_code": code, "expires": exp,
                "title": title, "percent": pct, "image": products[0]["image"],
                "product_count": len(products), "html": html_body}

    @app.post("/api/mail/test")
    async def mail_test(body: dict, key: str = ""):
        _check(key)
        cid = str(body.get("campaign_id") or "")
        email = str(body.get("email") or "").strip()
        if not cid or not email:
            raise HTTPException(status_code=400, detail="campaign_id and email required")
        mc = _mc()
        r = mc.post(f"{mc.base}/campaigns/{cid}/actions/test",
                    json={"test_emails": [email], "send_type": "html"}, timeout=40)
        if r.status_code >= 300:
            raise HTTPException(status_code=400, detail=str(r.text)[:200])
        return {"ok": True}

    @app.post("/api/mail/send")
    async def mail_send(body: dict, key: str = ""):
        _check(key)
        cid = str(body.get("campaign_id") or "")
        if not cid:
            raise HTTPException(status_code=400, detail="campaign_id required")
        mc = _mc()
        r = mc.post(f"{mc.base}/campaigns/{cid}/actions/send", timeout=40)
        if r.status_code >= 300:
            raise HTTPException(status_code=400, detail=str(r.text)[:200])
        return {"ok": True}

    @app.get("/api/mail/img")
    def mail_img(url: str = "", key: str = ""):
        _check(key)
        from fastapi.responses import Response
        try:
            r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
            return Response(content=r.content,
                            media_type=r.headers.get("Content-Type", "image/jpeg"))
        except Exception:
            raise HTTPException(status_code=404, detail="image fetch failed")

    @app.get("/story", response_class=HTMLResponse)
    def story(key: str = "", t: str = "", c: str = "", e: str = "", img: str = "", pct: str = "10"):
        _check(key)
        return HTMLResponse(_story_html(t, c, e, img, pct, key))
