"""
Private page to read customer↔bot WhatsApp conversations, served by the same
FastAPI app as the webhook. There is no native inbox for Cloud-API numbers, so
this reads the append-only conversation_log and groups it by phone number.

Routes (gated by ?key= == CATEGORY_TOOL_KEY / WhatsApp verify token):
    GET  /conversations         → the HTML page
    GET  /api/conv/threads      → conversations grouped by phone, newest first
"""
import os

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger
from src.whatsapp import conversation_log

logger = get_logger(__name__)


def _key() -> str:
    return (os.getenv("CATEGORY_TOOL_KEY") or os.getenv("WHATSAPP_VERIFY_TOKEN", "")).strip()


def _check(key: str) -> None:
    if not _key() or key != _key():
        raise HTTPException(status_code=403, detail="unauthorized")


def register_conversations_tool(app) -> None:
    @app.get("/conversations", response_class=HTMLResponse)
    def page(key: str = ""):
        _check(key)
        return HTMLResponse(_PAGE)

    @app.get("/api/conv/threads")
    def threads(key: str = ""):
        _check(key)
        msgs = conversation_log.read_all()
        by_phone: dict[str, list] = {}
        for m in msgs:
            by_phone.setdefault(m.get("phone", "?"), []).append(m)
        out = []
        for phone, items in by_phone.items():
            items.sort(key=lambda x: x.get("ts", ""))
            out.append({
                "phone": phone,
                "count": len([x for x in items if x.get("role") == "user"]),
                "last_ts": items[-1].get("ts", "") if items else "",
                "messages": items,
            })
        out.sort(key=lambda t: t["last_ts"], reverse=True)  # most recent first
        return {"threads": out, "total": len(out)}

    logger.info("Conversations tool mounted at /conversations")


_PAGE = r"""<!doctype html><html lang="he" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>שיחות עם סמדר AI</title>
<style>
:root{--bg:#f4f1ec;--surf:#fffdf9;--ink:#2c2a25;--muted:#77726a;--line:#e2dccf;--accent:#186a63;--user:#dcf3ee;--bot:#fff5e6}
@media(prefers-color-scheme:dark){:root{--bg:#15161a;--surf:#1e2025;--ink:#ecebe6;--muted:#9a978f;--line:#31343c;--accent:#54b7ae;--user:#1f3d38;--bot:#33291a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:"Assistant","Heebo",system-ui,Arial,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:18px 16px 60px}
h1{font-size:1.4rem;margin:0 0 4px}.sub{color:var(--muted);font-size:.9rem;margin-bottom:14px}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;position:sticky;top:0;background:var(--bg);padding:10px 0;z-index:5;border-bottom:1px solid var(--line)}
input[type=search]{flex:1;min-width:160px;font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:9px;background:var(--surf);color:var(--ink)}
.refresh{font:inherit;padding:8px 16px;border:1px solid var(--accent);color:var(--accent);background:transparent;border-radius:9px;cursor:pointer}
.count{color:var(--muted);font-size:.85rem}
.thread{background:var(--surf);border:1px solid var(--line);border-radius:14px;margin-top:12px;overflow:hidden}
.thead{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:12px 15px;cursor:pointer;user-select:none}
.phone{font-weight:700;direction:ltr}
.tmeta{font-size:.75rem;color:var(--muted)}
.body{padding:6px 14px 14px;display:none;flex-direction:column;gap:7px}
.thread.open .body{display:flex}
.msg{max-width:82%;padding:8px 12px;border-radius:13px;line-height:1.4;white-space:pre-wrap;word-break:break-word;font-size:.93rem}
.user{align-self:flex-start;background:var(--user);border-bottom-right-radius:4px}
.bot{align-self:flex-end;background:var(--bot);border-bottom-left-radius:4px}
.rec{align-self:center;font-size:.78rem;color:var(--muted);background:transparent;border:1px dashed var(--line);max-width:92%}
.time{font-size:.68rem;color:var(--muted);margin-top:2px}
.load{text-align:center;color:var(--muted);padding:30px}
</style></head><body><div class="wrap">
<h1>שיחות עם סמדר AI 💬</h1>
<div class="sub">כל השיחות של הלקוחות עם הבוט. לחץ על שיחה כדי לפתוח. (ירוק = לקוח · כתום = הבוט)</div>
<div class="bar">
  <input type="search" id="q" placeholder="חיפוש לפי מספר טלפון…">
  <button class="refresh" id="refresh">רענן 🔄</button>
  <span class="count" id="count"></span>
</div>
<div id="list"></div>
<div class="load" id="load">טוען…</div>
</div>
<script>
const KEY=new URLSearchParams(location.search).get("key")||"";
const $=s=>document.querySelector(s);
let THREADS=[];
function fmt(ts){try{const d=new Date(ts);return d.toLocaleString("he-IL",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"})}catch(e){return""}}
function esc(s){const d=document.createElement("div");d.textContent=s;return d.innerHTML}
function render(){
  const q=$("#q").value.trim();
  const list=$("#list");list.innerHTML="";
  const shown=THREADS.filter(t=>!q||t.phone.includes(q));
  $("#count").textContent=`${shown.length} שיחות`;
  for(const t of shown){
    const div=document.createElement("div");div.className="thread";
    const msgs=t.messages.map(m=>{
      const cls=m.role==="user"?"user":(m.role==="rec"?"rec":"bot");
      return `<div class="msg ${cls}">${esc(m.text)}<div class="time">${fmt(m.ts)}</div></div>`;
    }).join("");
    div.innerHTML=`<div class="thead"><span class="phone">+${esc(t.phone)}</span>
      <span class="tmeta">${t.count} הודעות · ${fmt(t.last_ts)}</span></div>
      <div class="body">${msgs}</div>`;
    div.querySelector(".thead").addEventListener("click",()=>div.classList.toggle("open"));
    list.appendChild(div);
  }
  if(!shown.length)list.innerHTML='<div class="load">אין עדיין שיחות. ברגע שלקוח יכתוב לבוט — זה יופיע כאן.</div>';
}
async function load(){
  $("#load").hidden=false;
  try{
    const r=await fetch("/api/conv/threads?key="+encodeURIComponent(KEY));
    if(!r.ok)throw new Error(await r.text());
    THREADS=(await r.json()).threads;
    render();
  }catch(e){$("#list").innerHTML='<div class="load">שגיאת הרשאה — ודא שהקישור כולל ?key=…</div>'}
  $("#load").hidden=true;
}
$("#q").addEventListener("input",render);
$("#refresh").addEventListener("click",load);
load();
</script></body></html>"""
