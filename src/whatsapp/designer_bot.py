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
# EDIT HERE to change payment info. These exact strings are sent by CODE (not
# written by the model) so links and account numbers can never be mangled.
_PAY_LINK = "https://meshulam.co.il/quick_payment?b=d132be4676e7ddd481668c4502c1b5fc"
_BIT_PHONE = "050-3356806"
_PAYMENT_BLOCKS = {
    "credit": f"💳 לתשלום מאובטח באשראי — בקישור:\n{_PAY_LINK}\n\nברגע שתסיים/י, שלח/י לי צילום מסך של האישור ואשריין לך את ההזמנה 🙌",
    "bit": f"📱 לתשלום בביט:\nאפשר דרך הקישור: {_PAY_LINK}\nאו העברת ביט ישירה למספר {_BIT_PHONE} (הגלריה לעיצוב הבית).\n\nאחרי התשלום שלח/י לי צילום אישור 🙏",
    "bank": "🏦 לתשלום בהעברה בנקאית:\n"
            "בנק מזרחי טפחות (20) · סניף 416 · חשבון 327065\n"
            "ע\"ש אזולאי דורון\n\n"
            "אחרי ההעברה אשמח שתשלח/י צילום אישור ואשריין את ההזמנה 🙏",
    "cash": f"💵 בתשלום מזומן:\nאפשר לשלם חצי מהסכום בקישור התשלום, וחצי במזומן לשליח בקבלת ההזמנה.\n\nהקישור לחצי הראשון:\n{_PAY_LINK}",
}

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

## תשלום — חשוב מאוד:
כשהלקוח מביע רצון לקנות/לשלם, שאל אותו איך נוח לו לשלם והצג את האפשרויות: **אשראי, ביט, העברה בנקאית, או מזומן**.
כשהוא בוחר אמצעי — כתוב משפט חם קצר (למשל "מעולה, שמחה לסגור! 😊") **והגדר את השדה "payment"** בערך המתאים.
⚠️ אל תכתוב בעצמך את הקישור או פרטי החשבון — המערכת מוסיפה אותם אוטומטית ובדיוק. אתה רק מגדיר את "payment":
- אשראי → "credit"
- ביט → "bit"
- העברה בנקאית → "bank"
- מזומן → "cash"
בכל שלב אחר — "payment" חייב להיות null.

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
  "payment": "credit"|"bit"|"bank"|"cash"|null
}}
- "search": מלא רק כשאתה ממליץ על מוצרים (1-2 קטגוריות); אחרת null. "keywords" אופציונלי.
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
        reply, search, payment = self._parse(raw)

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

        # Payment details are sent by CODE (exact link/account, never model-typed).
        if payment and payment in _PAYMENT_BLOCKS:
            block = _PAYMENT_BLOCKS[payment]
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
    def _parse(raw: str) -> tuple[str, Optional[dict], Optional[str]]:
        if not raw:
            return "", None, None
        txt = raw.strip()
        if txt.startswith("```"):
            txt = txt.strip("`")
            if txt.lower().startswith("json"):
                txt = txt[4:]
        try:
            data = json.loads(txt)
        except Exception:
            # Model didn't return JSON — treat the whole thing as the reply.
            return raw.strip(), None, None
        reply = (data.get("reply") or "").strip()
        search = data.get("search")
        if not isinstance(search, dict):
            search = None
        payment = data.get("payment")
        if payment not in _PAYMENT_BLOCKS:
            payment = None
        return reply, search, payment

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
