"""Deterministic safe drafts; an LLM adapter can enrich these later."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.marketing.models import ContentDraft, ContentSource

CHANNEL_FORMATS = (
    ("instagram", "post"),
    ("instagram", "reel"),
    ("instagram", "story"),
    ("facebook", "post"),
    ("meta", "ad"),
    ("google_ads", "responsive_search_ad"),
    ("wordpress", "article_outline"),
    ("email", "campaign"),
    ("whatsapp", "message"),
    ("seo", "faq"),
)


def with_utm(url: str | None, channel: str, campaign: str, variant: str) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({
        "utm_source": channel,
        "utm_medium": "organic" if channel in {"instagram", "facebook", "seo"} else "campaign",
        "utm_campaign": campaign,
        "utm_content": variant.lower(),
    })
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class ContentRepurposer:
    def __init__(self, brand_name: str, approved_claims: list[str]):
        self.brand_name = brand_name
        self.approved_claims = approved_claims

    def create_drafts(self, source: ContentSource, campaign: str = "content_repurpose") -> list[ContentDraft]:
        clean = " ".join(source.text.split()).strip()
        if not clean:
            raise ValueError("נדרש טקסט מקור; אין לייצר טענות ללא מקור")
        claim = self.approved_claims[0] if self.approved_claims else ""
        base = clean[:420]
        drafts: list[ContentDraft] = []
        for channel, output_format in CHANNEL_FORMATS:
            for variant, cta in (("A", "לצפייה בפרטים"), ("B", "גלו את הקולקציה")):
                headline = clean[:65] if variant == "A" else f"{self.brand_name} | {clean[:45]}"
                body = base + (f"\n\n{claim}" if claim and claim not in base else "")
                drafts.append(ContentDraft(
                    channel=channel,
                    format=output_format,
                    headline=headline,
                    body=body,
                    call_to_action=cta,
                    utm_url=with_utm(source.product_url, channel, campaign, variant),
                    variant=variant,
                    claims_used=[claim] if claim else [],
                ))
        return drafts
