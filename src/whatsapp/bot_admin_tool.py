# -*- coding: utf-8 -*-
"""
Live admin panel for the designer bot (סמדר AI), served by the same FastAPI app
as the webhook. One private page (gated by ?key=) to edit the bot WITHOUT a
redeploy — the personality/prompt, payment details, deposit %, and the category
skip-list — plus a live view of the categories the bot currently offers and a
button to clear the bot's cache so any change (including new store categories)
takes effect on the very next customer message.

Settings persist in WordPress (see bot_settings.py) so they survive redeploys.

Routes (key == BOT_ADMIN_KEY / CATEGORY_TOOL_KEY / WHATSAPP_VERIFY_TOKEN):
    GET  /bot                → the HTML page
    GET  /api/bot/get        → current settings + defaults + live categories
    POST /api/bot/save       → save edited settings and apply immediately
    POST /api/bot/reload     → clear caches (reload settings + categories)
"""
import os

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger
from src.whatsapp import bot_settings

logger = get_logger(__name__)


def _key() -> str:
    return (os.getenv("BOT_ADMIN_KEY") or os.getenv("CATEGORY_TOOL_KEY")
            or os.getenv("WHATSAPP_VERIFY_TOKEN", "")).strip()


def _check(key: str) -> None:
    if not _key() or key != _key():
        raise HTTPException(status_code=403, detail="unauthorized")


def register_bot_admin_tool(app, orchestrator) -> None:
    wc = orchestrator.wc_client
    cat_svc = orchestrator.category_svc

    def _designer():
        return getattr(orchestrator, "designer", None)

    def _live_categories():
        try:
            cat_svc.load()
            skip = set(bot_settings.get("skip_categories") or [])
            return sorted(n for n in (cat_svc.category_names or []) if n not in skip)
        except Exception as exc:
            logger.warning(f"[bot-admin] category list failed: {exc}")
            return []

    @app.get("/bot", response_class=HTMLResponse)
    def page(key: str = ""):
        _check(key)
        return HTMLResponse(_PAGE)

    @app.get("/api/bot/get")
    def get_settings(key: str = ""):
        _check(key)
        bot_settings.load(wc)                      # ensure loaded
        cur = bot_settings.current()
        return {
            "settings": {
                "system_prompt": cur.get("system_prompt", ""),
                "pay_link": cur.get("pay_link", ""),
                "bit_phone": cur.get("bit_phone", ""),
                "bank_details": cur.get("bank_details", ""),
                "deposit_pct": cur.get("deposit_pct", 0.5),
                "skip_categories": cur.get("skip_categories", []),
            },
            "defaults": {
                "system_prompt": bot_settings._DEFAULTS.get("system_prompt", ""),
            },
            "categories": _live_categories(),
        }

    @app.post("/api/bot/save")
    async def save_settings(request: Request):
        body = await request.json()
        _check(str(body.get("key", "")))
        updates = {}
        for k in ("system_prompt", "pay_link", "bit_phone", "bank_details"):
            if k in body:
                updates[k] = str(body[k])
        if "skip_categories" in body:
            updates["skip_categories"] = body["skip_categories"]   # str or list
        if "deposit_percent" in body:                              # panel sends 0-100
            try:
                updates["deposit_pct"] = float(body["deposit_percent"]) / 100.0
            except (TypeError, ValueError):
                pass
        try:
            bot_settings.save(wc, updates)
        except Exception as exc:
            logger.error(f"[bot-admin] save failed: {exc}")
            raise HTTPException(status_code=500, detail=f"save failed: {exc}")
        d = _designer()
        if d:
            d.reload_settings()                    # apply on the next message
        logger.info(f"[bot-admin] saved+applied: {list(updates.keys())}")
        return {"ok": True, "applied": bool(d), "categories": _live_categories()}

    @app.post("/api/bot/reload")
    async def reload(request: Request):
        body = await request.json()
        _check(str(body.get("key", "")))
        d = _designer()
        if d:
            d.reload_settings()
        return {"ok": True, "applied": bool(d), "categories": _live_categories()}

    logger.info("Bot admin panel mounted at /bot")


_PAGE = r"""<!doctype html><html lang="he" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ניהול הבוט — סמדר AI</title>
<style>
:root{--bg:#f4f1ec;--surf:#fffdf9;--ink:#2c2a25;--muted:#77726a;--line:#e2dccf;--accent:#7a3d63;--accent2:#a85a89;--ok:#3f7d4e;--warn:#9c6412}
@media(prefers-color-scheme:dark){:root{--bg:#15161a;--surf:#1e2025;--ink:#ecebe6;--muted:#9a978f;--line:#31343c;--accent:#cf90b4;--accent2:#e0a9c8;--ok:#63b177;--warn:#d69a4a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:"Assistant","Heebo",system-ui,Arial,sans-serif;line-height:1.55}
.wrap{max-width:840px;margin:0 auto;padding:20px 16px 80px}
h1{font-size:1.5rem;margin:0 0 2px}.sub{color:var(--muted);font-size:.9rem;margin-bottom:18px}
.card{background:var(--surf);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-bottom:14px}
.card h2{font-size:1.05rem;margin:0 0 4px}.hint{color:var(--muted);font-size:.82rem;margin:0 0 10px}
label{display:block;font-weight:600;font-size:.9rem;margin:10px 0 5px}
input,textarea{width:100%;font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--ink)}
textarea{resize:vertical;line-height:1.6}
textarea#system_prompt{min-height:300px;font-size:.9rem}
textarea#bank_details{min-height:70px}textarea#skip_categories{min-height:90px}
input:focus,textarea:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:transparent}
.row2{display:flex;gap:12px;flex-wrap:wrap}.row2>div{flex:1 1 200px}
.pct{max-width:120px}
.cats{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{font-size:.8rem;background:var(--bg);border:1px solid var(--line);border-radius:20px;padding:3px 11px}
.bar{position:sticky;bottom:0;background:color-mix(in srgb,var(--bg) 92%,transparent);backdrop-filter:blur(8px);border-top:1px solid var(--line);padding:12px 0;margin-top:8px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
button{font:inherit;font-weight:600;cursor:pointer;padding:11px 20px;border-radius:11px;border:1px solid var(--line);background:var(--surf);color:var(--ink)}
button.primary{background:var(--accent);color:#fff;border-color:transparent}
button.ghost{background:transparent}
button:disabled{opacity:.55;cursor:default}
.status{font-size:.88rem;color:var(--muted)}.status.ok{color:var(--ok)}.status.err{color:#c0392b}
.reset{font-size:.78rem;color:var(--accent);background:none;border:0;padding:2px 4px;cursor:pointer;font-weight:600}
.load{text-align:center;color:var(--muted);padding:40px}
</style></head><body><div class="wrap">
<h1>ניהול הבוט — סמדר AI</h1>
<div class="sub">כל שינוי נשמר בוורדפרס (שורד deploy) ומוחל מיד על ההודעה הבאה של לקוח. אין צורך בהעלאה מחדש.</div>
<div id="app" class="load">טוען…</div>

<div class="bar" id="bar" hidden>
  <button class="primary" id="save">שמור והחל עכשיו</button>
  <button class="ghost" id="reload">נקה מטמון / רענן קטגוריות</button>
  <span class="status" id="status"></span>
</div>
</div>
<script>
const KEY=new URLSearchParams(location.search).get("key")||"";
const $=s=>document.querySelector(s);
let DEFAULTS={};
async function api(u,opt){const r=await fetch(u,opt);if(!r.ok)throw new Error(await r.text());return r.json()}

function fieldsHtml(s){
  return `
  <div class="card">
    <h2>אישיות והוראות הבוט</h2>
    <p class="hint">מה הבוט אומר, הטון שלו, איך הוא מוכר וסוגר עסקה. חובה להשאיר את <code>{categories}</code> בטקסט — שם מוזרקת רשימת הקטגוריות.
      <button class="reset" id="resetPrompt" type="button">↺ שחזר לברירת מחדל</button></p>
    <textarea id="system_prompt">${escapeHtml(s.system_prompt)}</textarea>
  </div>
  <div class="card">
    <h2>תשלום ומקדמה</h2>
    <p class="hint">הבוט שולח בדיוק את הפרטים האלה — לא ממציא כלום.</p>
    <label>קישור תשלום (Meshulam)</label>
    <input id="pay_link" value="${escapeAttr(s.pay_link)}">
    <div class="row2">
      <div><label>מספר לביט</label><input id="bit_phone" value="${escapeAttr(s.bit_phone)}"></div>
      <div><label>מקדמה (%)</label><input id="deposit_percent" class="pct" type="number" min="1" max="100" value="${Math.round((s.deposit_pct||0.5)*100)}"></div>
    </div>
    <label>פרטי העברה בנקאית</label>
    <textarea id="bank_details">${escapeHtml(s.bank_details)}</textarea>
  </div>
  <div class="card">
    <h2>קטגוריות שהבוט לא מציע</h2>
    <p class="hint">שורה לכל קטגוריה (מבצעים/אוספים וכו'). כל השאר מוצעות ללקוח.</p>
    <textarea id="skip_categories">${escapeHtml((s.skip_categories||[]).join("\n"))}</textarea>
  </div>
  <div class="card">
    <h2>קטגוריות שהבוט מכיר עכשיו (<span id="catcount"></span>)</h2>
    <p class="hint">חי מהאתר. הוספת/שינית קטגוריה? לחץ "נקה מטמון" והיא תופיע כאן ותהיה זמינה לבוט.</p>
    <div class="cats" id="cats"></div>
  </div>`;
}
function escapeHtml(t){return (t||"").replace(/[&<>]/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[m]))}
function escapeAttr(t){return (t||"").replace(/[&<>"]/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[m]))}
function renderCats(cats){
  $("#catcount").textContent=cats.length;
  $("#cats").innerHTML=cats.map(c=>`<span class="chip">${escapeHtml(c)}</span>`).join("");
}
function setStatus(t,cls){const s=$("#status");s.textContent=t;s.className="status"+(cls?" "+cls:"")}

async function boot(){
  try{
    const d=await api("/api/bot/get?key="+encodeURIComponent(KEY));
    DEFAULTS=d.defaults||{};
    $("#app").innerHTML=fieldsHtml(d.settings);
    $("#app").className="";
    renderCats(d.categories||[]);
    $("#bar").hidden=false;
    $("#resetPrompt").addEventListener("click",()=>{
      if(DEFAULTS.system_prompt){$("#system_prompt").value=DEFAULTS.system_prompt;setStatus("שוחזר — לחץ שמור כדי להחיל");}
    });
  }catch(e){$("#app").innerHTML='<div class="card">שגיאת הרשאה — ודא שהקישור כולל ?key=…<br><small>'+escapeHtml(e.message)+'</small></div>'}
}
async function save(){
  const body={key:KEY,
    system_prompt:$("#system_prompt").value,
    pay_link:$("#pay_link").value.trim(),
    bit_phone:$("#bit_phone").value.trim(),
    bank_details:$("#bank_details").value,
    deposit_percent:$("#deposit_percent").value,
    skip_categories:$("#skip_categories").value};
  if(body.system_prompt.indexOf("{categories}")===-1){
    if(!confirm("שים לב: הסרת את {categories} מהפרומפט — הבוט לא יקבל את רשימת הקטגוריות. לשמור בכל זאת?"))return;
  }
  $("#save").disabled=true;setStatus("שומר…");
  try{
    const r=await api("/api/bot/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    renderCats(r.categories||[]);
    setStatus(r.applied?"נשמר והוחל ✓":"נשמר ✓ (יוחל בהודעה הבאה)","ok");
  }catch(e){setStatus("שמירה נכשלה: "+e.message,"err")}
  $("#save").disabled=false;
}
async function reload(){
  $("#reload").disabled=true;setStatus("מרענן…");
  try{
    const r=await api("/api/bot/reload",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({key:KEY})});
    renderCats(r.categories||[]);
    setStatus("המטמון נוקה — הקטגוריות עודכנו ✓","ok");
  }catch(e){setStatus("רענון נכשל: "+e.message,"err")}
  $("#reload").disabled=false;
}
$("#save").addEventListener("click",save);
$("#reload").addEventListener("click",reload);
boot();
</script></body></html>"""
