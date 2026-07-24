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

logger = get_logger(__name__)

# Categories we never offer as shopping destinations (sale/collection buckets).
_SKIP_CATS = {
    "New collection", "Sale", "SALE 70%", "70% הנחה", "Spring", "אחר",
    "ריהוט", "בדיקה ידנית", "Uncategorized", "ללא קטגוריה",
}

_SESSION_TTL = 60 * 60          # 1h of inactivity → fresh conversation
_MAX_TURNS = 12                 # keep the last N messages in context
_MAX_CARDS = 4                  # never spam more than this many products

_SYSTEM = """אתה "סמדר AI", מעצבת הפנים האישית ויועצת המכירות של חנות הריהוט והעיצוב "הגלריה לעיצוב הבית" של סמדר בטיטו (smadarbetitohome.co.il).
אתה מדבר עברית, בגוף ראשון, בחום ובגובה העיניים — כמו מעצבת אמיתית שרוצה לעזור ללקוח לעצב את הבית.
כשאתה ממליץ על מוצרים, הצג אותם כ"ההמלצות של סמדר" — כלומר בחירה אישית ומוקפדת מהחנות.

המטרה שלך: להבין מה הלקוח צריך, ואז להמליץ לו על מוצרים אמיתיים מהחנות שיתאימו לו.

איך לנהל את השיחה:
- אם חסר לך מידע — שאל שאלה אחת או שתיים קצרות וממוקדות בכל פעם (לא חקירה). מה שעוזר: לאיזה חדר? איזה סגנון (מודרני/כפרי/קלאסי/תעשייתי)? צבעים מועדפים? תקציב בערך? מידות או גודל החדר?
- אל תשאל יותר מדי — ברגע שיש לך מושג סביר על מה שהלקוח מחפש, המלץ. עדיף להמליץ ולדייק תוך כדי מאשר לתחקר.
- כשאתה ממליץ, כתוב משפט חם שמסביר למה בחרת ומה מתאים — ואל תמציא שמות מוצרים או מחירים. המוצרים עצמם יישלחו ללקוח אוטומטית מהקטלוג האמיתי אחרי ההודעה שלך.
- אם הלקוח שואל משהו כללי (שעות פתיחה, משלוחים, החזרות) — ענה בקצרה ובנעימות והצע להמשיך לעזור בעיצוב. אם אינך יודע פרט מסחרי מדויק, אמור שנציג אנושי יחזור אליו.
- לעולם אל תמליץ על קטגוריה שלא קיימת ברשימה שתקבל.

הקטגוריות הזמינות בחנות (בחר מתוכן בלבד כשאתה מחפש):
{categories}

אתה חייב להשיב אך ורק ב-JSON תקין במבנה הבא (בלי טקסט מסביב):
{{
  "reply": "<ההודעה בעברית שתישלח ללקוח>",
  "search": {{
     "categories": ["<שם קטגוריה מהרשימה>", "..."],
     "keywords": "<מילות חיפוש בעברית לשם המוצר, או ריק>",
     "min_price": <מספר או null>,
     "max_price": <מספר או null>
  }}
}}
כשעדיין אינך מוכן להמליץ (אתה שואל שאלה) — החזר "search": null.
כשאתה ממליץ — מלא את "search" עם 1-2 קטגוריות מתאימות. "keywords" אופציונלי; אם לא בטוח, השאר ריק ותסמוך על הקטגוריה."""


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

        session = self._session(from_number)
        session["history"].append({"role": "user", "content": text})
        session["history"] = session["history"][-_MAX_TURNS:]

        raw = self.ai.chat_messages(
            [{"role": "system", "content": self._system_prompt()}] + session["history"],
            max_tokens=700, temperature=0.6, json_mode=True,
        )
        reply, search = self._parse(raw)

        if not reply:
            reply = "אשמח לעזור לך לעצב! 🙂 מה אתה מחפש — לאיזה חדר, ובאיזה סגנון?"
        session["history"].append({"role": "assistant", "content": reply})

        self.wa.send_text(from_number, reply, reply_to_msg_id=message_id)

        if search:
            products = self._find(search)
            if products:
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
    def _parse(raw: str) -> tuple[str, Optional[dict]]:
        if not raw:
            return "", None
        txt = raw.strip()
        if txt.startswith("```"):
            txt = txt.strip("`")
            if txt.lower().startswith("json"):
                txt = txt[4:]
        try:
            data = json.loads(txt)
        except Exception:
            # Model didn't return JSON — treat the whole thing as the reply.
            return raw.strip(), None
        reply = (data.get("reply") or "").strip()
        search = data.get("search")
        if not isinstance(search, dict):
            search = None
        return reply, search

    def _find(self, search: dict) -> list:
        """Query the catalog for real, in-stock, published products.

        Tries category+keywords+price first, then relaxes (drops keywords, then
        drops category) so a customer rarely gets an empty-handed answer.
        """
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

        collected: dict[int, dict] = {}

        def query(category_id: Optional[str], keywords: str) -> None:
            if len(collected) >= _MAX_CARDS:
                return
            params: dict = {
                "per_page": 6, "status": "publish", "stock_status": "instock",
                "orderby": "popularity",
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
                if p.get("id") not in collected:
                    collected[p["id"]] = p

        # Pass 1: category + keywords
        for cid in (cat_ids or [None]):
            query(cid, kw)
        # Pass 2: category only (relax keywords)
        if len(collected) < 2 and kw:
            for cid in (cat_ids or [None]):
                query(cid, "")
        # Pass 3: keywords only, no category
        if len(collected) < 2 and kw:
            query(None, kw)

        return list(collected.values())[:_MAX_CARDS]

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
