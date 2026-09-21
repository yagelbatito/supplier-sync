"""FastAPI control-plane routes and a lightweight Hebrew RTL dashboard."""
from __future__ import annotations

import os

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from src.marketing.models import ApprovalDecision, ContentSource, PerformanceMetric
from src.marketing.service import MarketingPlatform

DASHBOARD_HTML = """<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Smadar Growth OS</title><style>
body{margin:0;background:#f5f1eb;color:#211f1c;font-family:Arial,sans-serif}header{padding:28px 5%;background:#182421;color:white}
main{padding:24px 5%;max-width:1200px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}
.card{background:white;border-radius:16px;padding:20px;box-shadow:0 5px 24px #0000000c}.safe{color:#237a56}.warn{color:#a56700}
button{background:#182421;color:white;border:0;border-radius:9px;padding:10px 16px}code{direction:ltr;display:block}
</style></head><body><header><h1>Smadar Growth OS</h1><p>מרכז השליטה השיווקי של הגלריה לעיצוב הבית</p></header>
<main><div class="grid"><section class="card"><h2>מצב בטיחות</h2><p class="safe">✓ Approval-first פעיל</p><p>תקרת תקציב: ₪5,000</p></section>
<section class="card"><h2>סוכנים</h2><p>תוכן · אנליטיקס · קמפיינים · מעורבות</p></section>
<section class="card"><h2>Meta MCP</h2><p class="warn">קריאות מותרות; כתיבות עוברות לאישור</p></section>
<section class="card"><h2>Google Ads</h2><p>Read-only לקיים · SYSTEM_TEST לחדש</p></section></div>
<section class="card" style="margin-top:16px"><h2>API פעיל</h2><code>GET /marketing/health · GET /marketing/actions · POST /marketing/content/repurpose</code></section></main></body></html>"""


def create_marketing_app(db_path: str = "data/marketing.db") -> FastAPI:
    app = FastAPI(title="Smadar Growth OS", version="0.1.0")
    platform = MarketingPlatform(db_path)
    router = APIRouter(prefix="/marketing")

    @app.get("/", response_class=HTMLResponse)
    def dashboard():
        return DASHBOARD_HTML

    @router.get("/health")
    def health():
        return {"status": "ok", "mode": "approval_first", "agents": platform.coordinator.registry}

    @router.get("/connections")
    def connections():
        specs = {
            "woocommerce": ["WC_URL", "WC_CONSUMER_KEY", "WC_CONSUMER_SECRET"],
            "meta": ["META_ADS_ACCESS_TOKEN", "META_AD_ACCOUNT_ID"],
            "google_ads": ["GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CUSTOMER_ID"],
            "merchant_center": ["GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"],
            "ga4": ["GA4_PROPERTY_ID"],
            "search_console": ["GSC_SITE_URL"],
            "email": ["BREVO_API_KEY"],
        }
        return [{
            "provider": provider,
            "status": "configured" if all(os.getenv(key) for key in required) else "not_configured",
            "mode": "read_only",
            "missing": [key for key in required if not os.getenv(key)],
        } for provider, required in specs.items()]

    @router.get("/actions")
    def actions():
        return [item.model_dump(mode="json") for item in platform.store.list_actions()]

    @router.get("/audit")
    def audit():
        return platform.store.audit()

    @router.post("/content/repurpose")
    def repurpose(source: ContentSource):
        return platform.coordinator.run("content_creator", {"source": source.model_dump()})

    @router.post("/analytics/rank")
    def rank(metrics: list[PerformanceMetric]):
        return platform.coordinator.run("analytics", {"metrics": [m.model_dump() for m in metrics]})

    @router.post("/campaigns/draft")
    def campaign_draft(task: dict):
        try:
            return platform.coordinator.run("ad_campaign_manager", task)
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @router.post("/engagement/draft")
    def engagement_draft(task: dict):
        try:
            return platform.coordinator.run("engagement", task)
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @router.post("/actions/{action_id}/decision")
    def decide(action_id: str, decision: ApprovalDecision):
        try:
            return platform.decide(action_id, decision).model_dump(mode="json")
        except KeyError:
            raise HTTPException(404, "הפעולה לא נמצאה")
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @router.post("/meta/tools/{tool_name}")
    def meta_tool(tool_name: str, arguments: dict):
        try:
            return platform.meta.call(tool_name, arguments)
        except PermissionError as exc:
            raise HTTPException(403, str(exc))

    app.include_router(router)
    return app
