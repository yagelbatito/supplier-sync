"""
Live category-management tool, served by the same FastAPI app as the WhatsApp
webhook (it already holds WC credentials). A private page lists managed
products, each with a category dropdown; changing it writes straight to
WooCommerce. Because every change lands in WC, the catalog itself becomes the
"training data" the importer learns from (see nearest_category()).

Routes (all gated by ?key= / body key == CATEGORY_TOOL_KEY, default: the
WhatsApp verify token):
    GET  /categories            → the HTML page
    GET  /api/cat/categories    → all WC category names (dropdown options)
    GET  /api/cat/products      → managed products (filter: supplier,q,category)
    POST /api/cat/set           → {id, category} update one product's category
"""
import os
import time

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from src.core.constants import META_SUPPLIER_NAME, META_SYNC_MANAGED
from src.core.logger import get_logger

logger = get_logger(__name__)
_cache = {"products": None, "ts": 0.0}
_TTL = 1800  # 30 min


def _key() -> str:
    return (os.getenv("CATEGORY_TOOL_KEY") or os.getenv("WHATSAPP_VERIFY_TOKEN", "")).strip()


def _check(key: str) -> None:
    if not _key() or key != _key():
        raise HTTPException(status_code=403, detail="unauthorized")


def register_category_tool(app, orchestrator) -> None:
    wc = orchestrator.wc_client
    cat_svc = orchestrator.category_svc

    def load_products(force: bool = False):
        if _cache["products"] is None or force or (time.time() - _cache["ts"] > _TTL):
            allp = wc.get_all("products", status="any")
            items = []
            for p in allp:
                meta = {m["key"]: m["value"] for m in p.get("meta_data", [])}
                if str(meta.get(META_SYNC_MANAGED, "")).lower() not in ("true", "1"):
                    continue
                cats = [c["name"] for c in p.get("categories", [])]
                items.append({
                    "id": p["id"], "name": p.get("name", ""),
                    "supplier": str(meta.get(META_SUPPLIER_NAME, "")) or "—",
                    "category": cats[0] if cats else "", "status": p.get("status"),
                })
            items.sort(key=lambda x: (x["category"] == "", x["category"], x["name"]))
            _cache["products"] = items
            _cache["ts"] = time.time()
        return _cache["products"]

    @app.get("/categories", response_class=HTMLResponse)
    def page(key: str = ""):
        _check(key)
        return HTMLResponse(_PAGE)

    @app.get("/api/cat/categories")
    def categories(key: str = ""):
        _check(key)
        cat_svc.load()
        skip = {"Sale", "SALE 70%", "70% הנחה", "New collection", "Spring", "Gift Card", "Cote Norie"}
        names = [n for n in cat_svc.category_names if n not in skip]
        # top-level categories = possible parents for a new sub-category
        tops = []
        try:
            allc = wc.get_all("products/categories")
            tops = sorted(c["name"] for c in allc if not c.get("parent") and c["name"] not in skip)
        except Exception as exc:
            logger.warning(f"[cat-tool] parents fetch failed: {exc}")
        return {"categories": sorted(names), "parents": tops}

    @app.post("/api/cat/newcat")
    async def new_category(request: Request):
        """Create a new store category (optionally under a parent) and return its
        name so the client can assign it to a product immediately."""
        body = await request.json()
        _check(str(body.get("key", "")))
        name = str(body.get("name", "")).strip()
        parent = str(body.get("parent", "")).strip()
        if not name:
            raise HTTPException(status_code=400, detail="missing name")
        cat_svc.load()
        if cat_svc.resolve(name, fallback=""):
            return {"ok": True, "name": name, "existed": True}
        pid = cat_svc.resolve(parent, fallback="") if parent else 0
        res = wc.post("products/categories", {"name": name, "parent": pid or 0})
        if res.get("id"):
            cat_svc._categories[name] = res["id"]      # warm the cache
        logger.info(f"[cat-tool] created category '{name}' (parent '{parent or '—'}')")
        return {"ok": True, "name": name, "id": res.get("id"), "existed": False}

    @app.get("/api/cat/products")
    def products(key: str = "", supplier: str = "", q: str = "", category: str = "",
                 offset: int = 0, limit: int = 60, refresh: int = 0):
        _check(key)
        items = load_products(force=bool(refresh))
        q = q.strip()
        f = [x for x in items
             if (not supplier or x["supplier"] == supplier)
             and (not category or x["category"] == category)
             and (not q or q in x["name"])]
        suppliers = sorted({x["supplier"] for x in items})
        return {"total": len(f), "items": f[offset:offset + limit],
                "suppliers": suppliers, "grand_total": len(items)}

    @app.post("/api/cat/set")
    async def set_category(request: Request):
        body = await request.json()
        _check(str(body.get("key", "")))
        pid = body.get("id")
        newcat = str(body.get("category", "")).strip()
        if not pid or not newcat:
            raise HTTPException(status_code=400, detail="missing id/category")
        cid = cat_svc.resolve(newcat)
        if not cid:
            raise HTTPException(status_code=400, detail="unknown category")
        wc.put(f"products/{pid}", {"categories": [{"id": cid}]})
        for x in (_cache["products"] or []):
            if str(x["id"]) == str(pid):
                x["category"] = newcat
                break
        logger.info(f"[cat-tool] product {pid} → {newcat}")
        return {"ok": True, "id": pid, "category": newcat}

    logger.info("Category tool mounted at /categories")


_PAGE = r"""<!doctype html><html lang="he" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ניהול קטגוריות</title>
<style>
:root{--bg:#f4f1ec;--surf:#fffdf9;--ink:#2c2a25;--muted:#77726a;--line:#e2dccf;--accent:#186a63;--ok:#3f7d4e;--warn:#9c6412}
@media(prefers-color-scheme:dark){:root{--bg:#15161a;--surf:#1e2025;--ink:#ecebe6;--muted:#9a978f;--line:#31343c;--accent:#54b7ae;--ok:#63b177;--warn:#d69a4a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:"Assistant","Heebo",system-ui,Arial,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:18px 16px 60px}
h1{font-size:1.4rem;margin:0 0 4px}.sub{color:var(--muted);font-size:.9rem;margin-bottom:14px}
.bar{display:flex;gap:8px;flex-wrap:wrap;position:sticky;top:0;background:var(--bg);padding:10px 0;z-index:5;border-bottom:1px solid var(--line)}
input,select{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:9px;background:var(--surf);color:var(--ink)}
input[type=search]{flex:1;min-width:160px}
.count{color:var(--muted);font-size:.85rem;align-self:center}
ul{list-style:none;margin:14px 0 0;padding:0;display:flex;flex-direction:column;gap:8px}
li{background:var(--surf);border:1px solid var(--line);border-radius:12px;padding:11px 14px;display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center}
.name{font-weight:600;line-height:1.3;word-break:break-word}
.meta{font-size:.72rem;color:var(--muted);margin-top:3px}
.pick{display:flex;align-items:center;gap:7px}
.pick select{min-width:190px;max-width:52vw}
.dot{width:9px;height:9px;border-radius:50%;flex:none;background:var(--line)}
.dot.saving{background:var(--warn)}.dot.saved{background:var(--ok)}.dot.err{background:#c0392b}
.empty select{border-color:var(--warn)}
.more{margin:18px auto 0;display:block;font:inherit;padding:11px 22px;border:1px solid var(--accent);color:var(--accent);background:transparent;border-radius:10px;cursor:pointer}
.load{text-align:center;color:var(--muted);padding:30px}
.newbtn{font:inherit;padding:8px 12px;border:1px solid var(--accent);color:var(--accent);background:transparent;border-radius:9px;cursor:pointer}
.newform{display:flex;gap:8px;flex-wrap:wrap;align-items:center;background:var(--surf);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin-top:10px}
.newform input,.newform select{flex:1 1 160px}
.newform button{font:inherit;padding:8px 14px;border-radius:9px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
.newform button.ghost{background:transparent;color:var(--accent)}
#ncmsg{font-size:.85rem;color:var(--muted)}
</style></head><body><div class="wrap">
<h1>ניהול קטגוריות</h1>
<div class="sub">שנה קטגוריה בדרופדאון — היא נשמרת מיד באתר. הנקודה ליד המוצר: 🟡 שומר · 🟢 נשמר.</div>
<div class="bar">
  <input type="search" id="q" placeholder="חיפוש לפי שם מוצר…">
  <select id="sup"><option value="">כל הספקים</option></select>
  <select id="onlyempty"><option value="">הכל</option><option value="1">רק בלי קטגוריה / בדיקה ידנית</option></select>
  <select id="pagesize"><option value="60">60 בעמוד</option><option value="150">150 בעמוד</option><option value="400">400 בעמוד</option></select>
  <button class="newbtn" id="newbtn" type="button">➕ קטגוריה חדשה</button>
  <span class="count" id="count"></span>
</div>
<div class="newform" id="newform" hidden>
  <input type="text" id="ncname" placeholder="שם הקטגוריה החדשה">
  <select id="ncparent"><option value="">ללא הורה (ראשית)</option></select>
  <button id="nccreate" type="button">צור</button>
  <button id="nccancel" type="button" class="ghost">ביטול</button>
  <span id="ncmsg"></span>
</div>
<ul id="list"></ul>
<div class="load" id="load" hidden>טוען…</div>
<button class="more" id="more" hidden>טען עוד</button>
</div>
<script>
const KEY=new URLSearchParams(location.search).get("key")||"";
let CATS=[], PARENTS=[], off=0, LIMIT=60, total=0, loading=false;
function syncPageSize(){LIMIT=parseInt($("#pagesize").value)||60}
const $=s=>document.querySelector(s);
async function api(u){const r=await fetch(u);if(!r.ok)throw new Error(await r.text());return r.json()}
function optionsHtml(cur){
  let o='<option value=""'+(cur?'':' selected')+'>— בחר —</option>';
  for(const c of CATS){o+=`<option${c===cur?' selected':''}>${c}</option>`}
  return o;
}
function row(p){
  const li=document.createElement("li");
  const empty=!p.category||p.category==="בדיקה ידנית";
  li.className=empty?"empty":"";
  li.innerHTML=`<div><div class="name">${p.name}</div>
    <div class="meta">${p.supplier} · ${p.status==='publish'?'מפורסם':p.status}</div></div>
    <div class="pick"><span class="dot" id="d${p.id}"></span>
    <select data-id="${p.id}">${optionsHtml(p.category)}</select></div>`;
  li.querySelector("select").addEventListener("change",e=>save(p.id,e.target.value,li));
  return li;
}
async function save(id,cat,li){
  const dot=$("#d"+id);dot.className="dot saving";
  try{
    const r=await fetch("/api/cat/set",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({key:KEY,id,category:cat})});
    if(!r.ok)throw new Error(await r.text());
    dot.className="dot saved";
    li.classList.toggle("empty", !cat||cat==="בדיקה ידנית");
  }catch(e){dot.className="dot err";alert("שמירה נכשלה: "+e.message)}
}
async function load(reset){
  if(loading)return;loading=true;$("#load").hidden=false;$("#more").hidden=true;
  if(reset){off=0;$("#list").innerHTML=""}
  const p=new URLSearchParams({key:KEY,offset:off,limit:LIMIT,
    supplier:$("#sup").value,q:$("#q").value.trim(),
    category:$("#onlyempty").value==="1"?"בדיקה ידנית":""});
  try{
    const d=await api("/api/cat/products?"+p);
    total=d.total;
    if(off===0 && $("#sup").options.length<2){
      for(const s of d.suppliers){const o=document.createElement("option");o.value=o.textContent=s;$("#sup").appendChild(o)}
    }
    const frag=document.createDocumentFragment();
    for(const it of d.items)frag.appendChild(row(it));
    $("#list").appendChild(frag);
    off+=d.items.length;
    $("#count").textContent=`מציג ${off} מתוך ${total} (סה״כ ${d.grand_total})`;
    $("#more").hidden = off>=total;
  }catch(e){$("#list").innerHTML='<li>שגיאה: '+e.message+' — בדוק שהקישור כולל ?key=…</li>'}
  loading=false;$("#load").hidden=true;
}
let t;function debounced(){clearTimeout(t);t=setTimeout(()=>load(true),350)}
$("#q").addEventListener("input",debounced);
$("#sup").addEventListener("change",()=>load(true));
$("#onlyempty").addEventListener("change",()=>load(true));
$("#pagesize").addEventListener("change",()=>{syncPageSize();load(true)});

// ── add-new-category ──
function fillParents(){
  const sel=$("#ncparent");
  sel.innerHTML='<option value="">ללא הורה (ראשית)</option>'+PARENTS.map(p=>`<option>${p}</option>`).join("");
}
$("#newbtn").addEventListener("click",()=>{const f=$("#newform");f.hidden=!f.hidden;if(!f.hidden){fillParents();$("#ncname").focus()}});
$("#nccancel").addEventListener("click",()=>{$("#newform").hidden=true;$("#ncname").value="";$("#ncmsg").textContent=""});
$("#nccreate").addEventListener("click",async()=>{
  const name=$("#ncname").value.trim(); const parent=$("#ncparent").value;
  if(!name){$("#ncmsg").textContent="הזן שם";return}
  $("#nccreate").disabled=true;$("#ncmsg").textContent="יוצר…";
  try{
    const r=await fetch("/api/cat/newcat",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({key:KEY,name,parent})});
    if(!r.ok)throw new Error(await r.text());
    if(!CATS.includes(name)){CATS.push(name);CATS.sort((a,b)=>a.localeCompare(b,"he"));}
    // add the new option to every product dropdown already on screen
    document.querySelectorAll('#list select').forEach(s=>{
      if(![...s.options].some(o=>o.value===name)){
        const o=document.createElement("option");o.textContent=name;o.value=name;s.appendChild(o);
      }
    });
    $("#ncmsg").textContent="נוצר ✓ — בחר אותה בדרופדאון של המוצר";
    $("#ncname").value="";
  }catch(e){$("#ncmsg").textContent="נכשל: "+e.message}
  $("#nccreate").disabled=false;
});
$("#more").addEventListener("click",()=>load(false));
(async()=>{try{const d=await api("/api/cat/categories?key="+encodeURIComponent(KEY));CATS=d.categories;PARENTS=d.parents||[];await load(true)}
catch(e){$("#list").innerHTML='<li>שגיאת הרשאה — ודא שהקישור כולל ?key=…</li>'}})();
</script></body></html>"""
