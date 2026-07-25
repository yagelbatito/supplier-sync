"""
Customer-facing AI interior-designer bot.

Runs for NON-owner numbers (real customers). It holds a short conversation,
understands the customer's needs (room, style, colours, budget, size), and
recommends REAL products from the WooCommerce catalog — with photo, price and
a link to buy.

Design (why it's built this way):
  * The store's OpenAIClient wrapper is a thin chat shim, so instead of the
    heavier function-calling loop we drive everything with ONE json-mode call
    per turn. The model returns {"reply", "search": {...}|null}. When `search`
    is present we query WC and send product cards after the text reply. This
    keeps the model in charge of the conversation (it asks clarifying
    questions by returning search=null) while we stay in charge of the catalog
    (only real, in-stock, published products are ever shown).
  * Conversation memory is in-process (one Render worker). Sessions expire so
    a returning customer starts fresh after a while.
"""
import json
import time
from typing import Optional

from src.core.logger import get_logger
from src.whatsapp.conversation_log import log_message

logger = get_logger(__name__)

# Categories we never offer as shopping destinations (sale/collection buckets).
_SKIP_CATS = {
    "New collection", "Sale", "SALE 70%", "70% הנחה", "Spring", "אחר",
    "ריהוט", "בדיקה ידנית", "Uncategorized", "ללא קטגוריה",
}

_SESSION_TTL = 60 * 60          # 1h of inactivity → fresh conversation
_MAX_TURNS = 12                 # keep the last N messages in context
_MAX_CARDS = 4                  # never spam more than this many products
_POOL_SIZE = 30                 # candidate pool the reranker chooses from

# ── Payment details ──────────────────────────────────────────────
# EDIT HERE to change payment info. Sent by CODE (not written by the model) so
# links / account numbers / amounts can never be mangled.
_PAY_LINK = "https://meshulam.co.il/quick_payment?b=d132be4676e7ddd481668c4502c1b5fc"
_BIT_PHONE = "050-3356806"
_BANK_DETAILS = ("בנק מזרחי טפחות (20) · סניף 416 · חשבון 327065\n"
                 "ע\"ש אזולאי דורון")
_PAY_METHODS = {"credit", "bit", "bank", "cash"}
_DEPOSIT_PCT = 0.5   # a 50% deposit is the minimum to open an order


def _money(v) -> str:
    """Format a price as whole shekels (no trailing decimals)."""
    try:
        return f"{int(round(float(v))):,}"
    except (ValueError, TypeError):
        return str(v)

_SYSTEM = """אתה "סמדר AI", מעצבת הפנים האישית ואשת המכירות של חנות הריהוט והעיצוב "הגלריה לעיצוב הבית" של סמדר בטיטו (smadarbetitohome.co.il).
אתה מדבר עברית, בגוף ראשון, בחום ובגובה העיניים — כמו מעצבת אמיתית שאוהבת לעזור ללקוח לעצב את הבית, וגם יודעת לסגור עסקה בנעימות ובביטחון.
כשאתה ממליץ על מוצרים, הצג אותם כ"ההמלצות של סמדר" — בחירה אישית ומוקפדת מהחנות.

## התפקיד שלך (3 דברים):
1. **לעצב ולהמליץ** — להבין מה הלקוח צריך ולהמליץ על מוצרים אמיתיים מהחנות שיתאימו לו.
2. **למכור ולהניע לסגירה** — אחרי שהמלצת, קדם את העסקה בעדינות: "רוצה שאשמור לך?", "בא לך שנסגור עכשiu? 😊", הדגש איכות, מלאי מוגבל, ומשלוח עד הבית. בטוח אבל לא לוחץ.
3. **להוביל לתשלום** — כשהלקוח רוצה לקנות, הובל אותו לתשלום לפי האמצעי שנוח לו.

## איך לנהל את השיחה:
- אם חסר מידע — שאל שאלה אחת-שתיים קצרות (חדר, סגנון, צבע, תקציב, מידות). אל תחקור יותר מדי; ברגע שיש מושג סביר — המלץ.
- כתוב חם, אישי ובמשפטים קצרים. אל תמציא שמות מוצרים או מחירים — המוצרים נשלחים אוטומטית מהקטלוג אחרי ההודעה שלך.
- כשהלקוח מתלבט — תן ביטחון והצע חלופה, בלי ללחוץ בכוח.
- לעולם אל תמליץ על קטגוריה שלא קיימת ברשימה.

## קנייה ותשלום — חשוב מאוד:
- כשהלקוח רוצה לקנות מוצר מסוים, **או שואל כמה זה עולה / כמה לשלם** — **הגדר את השדה "order"** עם שם המוצר ואת **הכמות** (qty) שהוא ביקש (אם ציין "3 כיסאות" → qty:3; אם לא ציין — qty:1). שמור על "reply" קצר וחם ("איזה כיף, בחירה מצוינת! 😊"). ⚠️ **אל תכתוב מחירים או סכומים בעצמך** — המערכת מחשבת ומודיעה ללקוח את המחיר הכולל, את גובה המקדמה (50% — המינימום לפתיחת הזמנה), ומבקשת אמצעי תשלום.
- כשהלקוח בוחר אמצעי תשלום — **הגדר את "payment"** ו-"reply" קצר וחם ("מעולה, שמחה לסגור! 😊"). המערכת תוסיף את פרטי התשלום המדויקים ואת הסכום.
  - אשראי → "credit" · ביט → "bit" · העברה בנקאית → "bank" · מזומן → "cash"
- מדיניות התשלום שלנו: לפתיחת הזמנה נדרשת מקדמה של חצי מהסכום. במזומן — חצי מראש (אשראי/ביט/העברה, חובה) והיתרה במזומן לשליח בקבלת ההזמנה.
- ⚠️ אל תכתוב בעצמך קישורים, פרטי חשבון או סכומים — המערכת עושה זאת מדויק.
- "order" ו-"payment" = null בכל שלב אחר.

## הקטגוריות הזמינות בחנות (בחר מתוכן בלבד כשאתה מחפש):
{categories}

## פורמט התשובה — חובה JSON תקין בלבד (בלי טקסט מסביב):
{{
  "reply": "<ההודעה בעברית שתישלח ללקוח>",
  "search": {{
     "categories": ["<שם קטגוריה מהרשימה>", "..."],
     "keywords": "<מילות חיפוש בעברית לשם המוצר, או ריק>",
     "min_price": <מספר או null>,
     "max_price": <מספר או null>
  }},
  "order": {{"product": "<שם המוצר>", "qty": <כמות, ברירת מחדל 1>}} או null,
  "payment": "credit"|"bit"|"bank"|"cash"|null
}}
- "search": מלא רק כשאתה ממליץ על מוצרים (1-2 קטגוריות); אחרת null. "keywords" אופציונלי.
- "order": מלא כשהלקוח רוצה לקנות מוצר ספציפי או שואל את מחירו/כמה לשלם (כולל הכמות); אחרת null.
- "payment": מלא רק כשהלקוח בחר אמצעי תשלום; אחרת null."""


class DesignerBot:
    def __init__(self, wa_client, wc_client, category_svc, ai_client):
        self.wa = wa_client
        self.wc = wc_client
        self.category_svc = category_svc
        self.ai = ai_client
        self._sessions: dict[str, dict] = {}
        self._cats_cache: Optional[str] = None

    # ── public entry ─────────────────────────────────────────────
    def handle(self, from_number: str, text: str, message_id: Optional[str] = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        if not self.ai.available:
            self.wa.send_text(
                from_number,
                "שלום! 🙂 תודה שפנית להגלריה לעיצוב הבית. נציג יחזור אליך בהקדם.",
                reply_to_msg_id=message_id,
            )
            return

        log_message(from_number, "user", text)

        session = self._session(from_number)
        session["history"].append({"role": "user", "content": text})
        session["history"] = session["history"][-_MAX_TURNS:]

        raw = self.ai.chat_messages(
            [{"role": "system", "content": self._system_prompt()}] + session["history"],
            max_tokens=700, temperature=0.6, json_mode=True,
        )
        reply, search, order, payment = self._parse(raw)

        if not reply:
            reply = "אשמח לעזור לך לעצב! 🙂 מה אתה מחפש — לאיזה חדר, ובאיזה סגנון?"
        session["history"].append({"role": "assistant", "content": reply})
        log_message(from_number, "bot", reply)

        self.wa.send_text(from_number, reply, reply_to_msg_id=message_id)

        if search:
            # request_text = what the customer actually asked, so the reranker
            # can judge closeness (colour/material/style) — not just category.
            recent_user = [m["content"] for m in session["history"] if m["role"] == "user"][-2:]
            request_text = " | ".join(recent_user) or text
            products = self._recommend(search, request_text)
            if products:
                # Remember what we showed (with prices) so a later "I'll buy X"
                # can be priced without another lookup.
                session["last_products"] = [
                    {"id": p.get("id"), "name": p.get("name", ""),
                     "price": p.get("price") or p.get("regular_price")}
                    for p in products
                ]
                names = ", ".join(p.get("name", "") for p in products)
                log_message(from_number, "rec", f"המלצות שנשלחו: {names}")
                for p in products:
                    self._send_card(from_number, p)
            else:
                # We promised products but found none — soften it, don't leave
                # the customer hanging on an empty recommendation.
                self.wa.send_text(
                    from_number,
                    "לא מצאתי כרגע פריט שמתאים בול לזה במלאי — רוצה שאבדוק כיוון אחר? "
                    "אפשר לשנות סגנון, צבע או תקציב.",
                )

        # Customer chose a product to buy → state price × qty + 50% deposit.
        if order:
            prod = self._resolve_price(session, order["product"])
            if prod and prod.get("price"):
                qty = order["qty"]
                unit = float(prod["price"])
                total = unit * qty
                dep = int(round(total * _DEPOSIT_PCT))
                prev = session.get("order")
                session["order"] = {"id": prod.get("id"), "name": prod.get("name", ""),
                                    "unit": unit, "qty": qty, "total": total}
                # Don't repeat the price line if it's the same order restated in
                # the same breath as choosing a payment method.
                same = prev and prev.get("id") == prod.get("id") and prev.get("qty") == qty
                if not (same and payment):
                    if qty > 1:
                        line = f"{qty} × {prod['name']} ({_money(unit)} ₪ ליחידה) = *{_money(total)} ₪*."
                    else:
                        line = f"מחיר {prod['name']} — *{_money(total)} ₪*."
                    msg = (f"{line}\nלפתיחת הזמנה נדרשת מקדמה של 50%: *{_money(dep)} ₪*.\n\n"
                           f"איך נוח לך לשלם? אשראי 💳 / ביט 📱 / העברה בנקאית 🏦 / מזומן 💵")
                    log_message(from_number, "bot", msg)
                    self.wa.send_text(from_number, msg)
            else:
                self.wa.send_text(
                    from_number,
                    "אשמח לעזור לך לסגור! על איזה מוצר בדיוק מדובר? "
                    "כתוב/כתבי לי את שם הדגם ואבדוק לך מחיר וזמינות. 🙂",
                )

        # Payment details are sent by CODE (exact link/account/amount, never
        # model-typed) using the selected order's total for the 50% deposit.
        if payment and payment in _PAY_METHODS:
            order_p = session.get("order")
            total = dep = None
            if order_p and order_p.get("total"):
                total = float(order_p["total"])
                dep = int(round(total * _DEPOSIT_PCT))
            block = self._payment_text(payment, total, dep)
            log_message(from_number, "bot", block)
            self.wa.send_text(from_number, block)

    # ── internals ────────────────────────────────────────────────
    def _session(self, phone: str) -> dict:
        now = time.time()
        s = self._sessions.get(phone)
        if not s or now - s.get("updated", 0) > _SESSION_TTL:
            s = {"history": [], "updated": now}
            self._sessions[phone] = s
        s["updated"] = now
        return s

    def _system_prompt(self) -> str:
        if self._cats_cache is None:
            names = [n for n in (self.category_svc.category_names or []) if n not in _SKIP_CATS]
            self._cats_cache = "، ".join(names) if names else "ספות, כורסאות, שולחנות, מראות, תאורה, שטיחים, כריות נוי"
        return _SYSTEM.format(categories=self._cats_cache)

    @staticmethod
    def _parse(raw: str) -> tuple[str, Optional[dict], Optional[dict], Optional[str]]:
        if not raw:
            return "", None, None, None
        txt = raw.strip()
        if txt.startswith("```"):
            txt = txt.strip("`")
            if txt.lower().startswith("json"):
                txt = txt[4:]
        try:
            data = json.loads(txt)
        except Exception:
            # Model didn't return JSON — treat the whole thing as the reply.
            return raw.strip(), None, None, None
        reply = (data.get("reply") or "").strip()
        search = data.get("search")
        if not isinstance(search, dict):
            search = None
        order = DesignerBot._parse_order(data.get("order"))
        payment = data.get("payment")
        if payment not in _PAY_METHODS:
            payment = None
        return reply, search, order, payment

    @staticmethod
    def _parse_order(raw) -> Optional[dict]:
        """Normalize the order field to {product, qty} or None.

        Accepts either an object {"product","qty"} or a bare product string
        (older/looser model output), defaulting qty to 1.
        """
        product, qty = None, 1
        if isinstance(raw, dict):
            product = raw.get("product") or raw.get("name")
            qty = raw.get("qty") or raw.get("quantity") or 1
        elif isinstance(raw, str):
            product = raw
        if not isinstance(product, str) or not product.strip():
            return None
        try:
            qty = max(1, int(float(qty)))
        except (ValueError, TypeError):
            qty = 1
        return {"product": product.strip(), "qty": qty}

    def _recommend(self, search: dict, request_text: str) -> list:
        """Return the products CLOSEST to what the customer asked for.

        Two stages: (1) pull a real, in-stock candidate pool from the catalog
        (keyword hits first, then the whole category within budget), then
        (2) let the model rank that pool by closeness to the request — colour,
        material, tone, style — so "ספה דמוי עור סהרה" surfaces the sand/beige
        leather-look sofas we DO carry rather than random ones. Only ever shows
        real products; the model just picks among them.
        """
        pool = self._candidates(search)
        if not pool:
            return []
        ranked = self._rerank(request_text, pool)
        return ranked[:_MAX_CARDS]

    def _candidates(self, search: dict) -> list:
        """Pull a real, in-stock, published candidate pool from the catalog."""
        cats = [c for c in (search.get("categories") or []) if c][:3]
        kw = (search.get("keywords") or "").strip()
        min_p = self._num(search.get("min_price"))
        max_p = self._num(search.get("max_price"))

        cat_ids: list[str] = []
        for name in cats:
            # fallback="" so an unknown name returns None instead of snapping to
            # the "בדיקה ידנית" review bucket (which is empty for customers).
            cid = self.category_svc.resolve(name, fallback="")
            if cid:
                cat_ids.append(str(cid))

        pool: dict[int, dict] = {}

        def query(category_id: Optional[str], keywords: str, per_page: int) -> None:
            if len(pool) >= _POOL_SIZE:
                return
            params: dict = {
                "per_page": per_page, "status": "publish",
                "stock_status": "instock", "orderby": "popularity",
            }
            if keywords:
                params["search"] = keywords
            if category_id:
                params["category"] = category_id
            if min_p:
                params["min_price"] = str(min_p)
            if max_p:
                params["max_price"] = str(max_p)
            try:
                res = self.wc.get("products", params=params) or []
            except Exception as exc:
                logger.error(f"[designer] search failed: {exc}")
                res = []
            for p in res:
                pool.setdefault(p.get("id"), p)

        # 1. keyword hits within each category — strongest signal, listed first
        if kw:
            for cid in (cat_ids or [None]):
                query(cid, kw, 10)
        # 2. broaden: the whole category within budget — gives the reranker a
        #    rich pool of close alternatives when the exact item isn't in stock
        for cid in (cat_ids or [None]):
            query(cid, "", 20)
        # 3. last resort: keyword-only across the store
        if not pool and kw:
            query(None, kw, 10)

        return list(pool.values())[:_POOL_SIZE]

    def _rerank(self, request_text: str, products: list) -> list:
        """Order the pool by closeness to the customer's request (AI-judged).

        Falls back to the pool's own order if the model is unavailable or the
        response can't be parsed — we still return real products either way.
        """
        if len(products) <= _MAX_CARDS or not self.ai.available:
            return products
        lines = "\n".join(
            f"{i}. {p.get('name','')} — {p.get('price') or p.get('regular_price') or '?'}₪"
            for i, p in enumerate(products)
        )
        prompt = (
            f"הלקוח מחפש: {request_text}\n\n"
            f"אלה המוצרים הזמינים בחנות (מספר. שם — מחיר):\n{lines}\n\n"
            f"בחר עד {_MAX_CARDS} מוצרים שהכי קרובים למה שהלקוח רוצה — "
            f"התאמה לפי צבע, גוון, חומר, סגנון וגודל. דרג מהקרוב ביותר לפחות. "
            f"אם מוצר ממש לא רלוונטי, אל תכלול אותו. "
            f'החזר רק JSON: {{"picks": [מספרי המוצרים לפי הסדר]}}'
        )
        raw = self.ai.chat(
            system_prompt="אתה עוזר בחירת מוצרים לחנות עיצוב. ענה אך ורק ב-JSON תקין.",
            user_prompt=prompt, max_tokens=60, temperature=0, json_mode=True,
        )
        try:
            picks = json.loads(raw).get("picks", [])
        except Exception:
            return products
        ordered = [products[i] for i in picks if isinstance(i, int) and 0 <= i < len(products)]
        return ordered or products

    def _resolve_price(self, session: dict, name: str) -> Optional[dict]:
        """Find the product (and its price) the customer wants to buy.

        Robust to loose/misspelled names (e.g. "כיסא בר טיטי כאמל" vs the real
        "כסא בר טיטי קאמל"): casts a wide net (full phrase + each significant
        word) then fuzzy-ranks the pool to pick the closest — not just the first
        search hit. Prefers a just-recommended product when it clearly matches.
        Returns {id, name, price} or None.
        """
        from thefuzz import fuzz
        name = (name or "").strip()
        if not name:
            return None

        # 1. Build a candidate pool from the catalog (+ recently recommended).
        pool: dict = {}
        for p in session.get("last_products", []):
            if p.get("id") and p.get("price"):
                pool[p["id"]] = {"id": p["id"], "name": p.get("name", ""), "price": p["price"]}
        queries = [name] + [w for w in name.split() if len(w) >= 3]
        for q in queries:
            if len(pool) >= 20:
                break
            try:
                res = self.wc.get("products", params={
                    "search": q, "per_page": 6,
                    "status": "publish", "stock_status": "instock",
                }) or []
            except Exception as exc:
                logger.error(f"[designer] price lookup failed: {exc}")
                res = []
            for p in res:
                pid = p.get("id")
                if pid and pid not in pool:
                    pool[pid] = {"id": pid, "name": p.get("name", ""),
                                 "price": p.get("price") or p.get("regular_price")}

        # 2. Pick the closest by fuzzy token match; require a reasonable score.
        best, best_score = None, -1
        for p in pool.values():
            if not p.get("price"):
                continue
            sc = fuzz.token_set_ratio(name, p.get("name", ""))
            if sc > best_score:
                best, best_score = p, sc
        if best and best_score >= 55:
            return best
        return None

    @staticmethod
    def _payment_text(method: str, total: Optional[float], dep: Optional[int]) -> str:
        """Build the exact payment message, with the deposit amount when known."""
        have = total is not None and dep is not None
        rest = int(round(total - dep)) if have else None
        if method == "credit":
            head = f"💳 לתשלום מקדמה של {_money(dep)} ₪ באשראי" if have else "💳 לתשלום מאובטח באשראי"
            return f"{head} — בקישור:\n{_PAY_LINK}\n\nברגע שתסיים/י שלח/י צילום אישור ואשריין את ההזמנה 🙌"
        if method == "bit":
            head = f"📱 לתשלום מקדמה של {_money(dep)} ₪ בביט" if have else "📱 לתשלום בביט"
            return (f"{head}:\nדרך הקישור: {_PAY_LINK}\n"
                    f"או העברת ביט ישירה למספר {_BIT_PHONE} (הגלריה לעיצוב הבית).\n\n"
                    f"אחרי התשלום שלח/י צילום אישור 🙏")
        if method == "bank":
            head = f"🏦 להעברת מקדמה של {_money(dep)} ₪" if have else "🏦 לתשלום בהעברה בנקאית"
            return f"{head}:\n{_BANK_DETAILS}\n\nאחרי ההעברה אשמח שתשלח/י צילום אישור ואשריין את ההזמנה 🙏"
        if method == "cash":
            if have:
                return (f"💵 בתשלום מזומן:\nלפתיחת הזמנה יש לשלם מקדמה של *{_money(dep)} ₪* (חצי) מראש — "
                        f"באשראי, ביט או העברה בנקאית (חובה לפתיחת ההזמנה).\n"
                        f"את היתרה ({_money(rest)} ₪) משלמים במזומן לשליח בקבלת ההזמנה.\n\n"
                        f"הקישור לתשלום המקדמה:\n{_PAY_LINK}")
            return (f"💵 בתשלום מזומן:\nלפתיחת הזמנה יש לשלם חצי מהסכום מראש — באשראי, ביט או העברה בנקאית "
                    f"(חובה לפתיחת ההזמנה), והיתרה במזומן לשליח בקבלת ההזמנה.\n\n"
                    f"הקישור לתשלום המקדמה:\n{_PAY_LINK}")
        return ""

    def _send_card(self, to: str, p: dict) -> None:
        name = p.get("name", "")
        price = p.get("price") or p.get("regular_price") or ""
        caption = f"🛋️ {name}"
        if price:
            caption += f"\n💰 {price} ₪"
        if p.get("permalink"):
            caption += f"\n🔗 {p['permalink']}"
        imgs = p.get("images") or []
        if imgs and imgs[0].get("src"):
            self.wa.send_image(to, imgs[0]["src"], caption)
        else:
            self.wa.send_text(to, caption)

    @staticmethod
    def _num(v) -> Optional[int]:
        try:
            if v is None:
                return None
            return int(float(v))
        except (ValueError, TypeError):
            return None
