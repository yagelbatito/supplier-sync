# -*- coding: utf-8 -*-
"""
Fill the Mailchimp PHONE field from WooCommerce billing phones.

The Mailchimp-for-WooCommerce plugin syncs email/name/address but not the phone,
so the audience's Phone Number field is empty. This maps WooCommerce order
billing phones to Mailchimp contacts by email and fills the PHONE merge field
(existing contacts only; never overwrites a phone already there). Enables SMS /
WhatsApp follow-up on the same audience.

Env: MAILCHIMP_API_KEY (+ MAILCHIMP_LIST_ID), WOOCOMMERCE_*/WP_*.
    python sync_phones_to_mailchimp.py
"""
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

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _mc(dc, key):
    s = requests.Session()
    s.auth = ("anystring", key)
    s.headers.update({"User-Agent": UA, "Content-Type": "application/json"})
    s.base = f"https://{dc}.api.mailchimp.com/3.0"
    return s


def _norm_phone(ph):
    d = re.sub(r"\D", "", ph or "")
    if d.startswith("972"):
        d = "0" + d[3:]
    return d if (len(d) == 10 and d.startswith("0")) else ""


def wc_email_to_phone(c):
    """Map lowercased email → normalized Israeli phone, from all orders."""
    emap, page = {}, 1
    while True:
        batch = None
        for attempt in range(4):
            try:
                batch = c.get("orders", params={"per_page": 100, "page": page,
                                                "orderby": "date", "order": "asc"})
                break
            except Exception:
                if attempt == 3:
                    raise
        if not batch:
            break
        for o in batch:
            b = o.get("billing", {})
            em = (b.get("email") or "").strip().lower()
            ph = _norm_phone(b.get("phone"))
            if em and ph:
                emap[em] = ph          # asc order → latest order wins
        if len(batch) < 100:
            break
        page += 1
    return emap


def mc_members(mc, list_id):
    """Yield all members (email, id, current PHONE)."""
    offset = 0
    while True:
        r = mc.get(f"{mc.base}/lists/{list_id}/members",
                   params={"count": 1000, "offset": offset,
                           "fields": "members.email_address,members.id,members.merge_fields.PHONE"},
                   timeout=60).json()
        batch = r.get("members", [])
        if not batch:
            break
        for m in batch:
            yield m
        offset += len(batch)
        if len(batch) < 1000:
            break


def main():
    key = os.getenv("MAILCHIMP_API_KEY", "").strip()
    if not key or "-" not in key:
        print("MAILCHIMP_API_KEY missing.", flush=True); sys.exit(1)
    dc = key.rsplit("-", 1)[1]
    list_id = os.getenv("MAILCHIMP_LIST_ID", "").strip()
    mc = _mc(dc, key)
    if not list_id:
        lists = mc.get(f"{mc.base}/lists?count=5&fields=lists.id", timeout=40).json().get("lists", [])
        list_id = lists[0]["id"] if lists else ""

    s = load_app_settings()
    c = WooCommerceClient(url=s.woocommerce_url, consumer_key=s.woocommerce_key,
                          consumer_secret=s.woocommerce_secret, wp_user=s.wp_user,
                          wp_app_password=s.wp_app_password, verify_ssl=s.verify_ssl, dry_run=True)

    emap = wc_email_to_phone(c)
    print(f"WooCommerce email→phone map: {len(emap)}", flush=True)

    updated = skipped_has = no_phone = failed = 0
    for m in mc_members(mc, list_id):
        em = m["email_address"].lower()
        if (m.get("merge_fields", {}).get("PHONE") or "").strip():
            skipped_has += 1
            continue
        ph = emap.get(em)
        if not ph:
            no_phone += 1
            continue
        r = mc.patch(f"{mc.base}/lists/{list_id}/members/{m['id']}",
                     json={"merge_fields": {"PHONE": ph}}, timeout=30)
        if r.status_code < 300:
            updated += 1
        else:
            failed += 1
            if failed <= 5:
                print(f"  fail {em[:20]}: {r.status_code} {r.text[:120]}", flush=True)
        if (updated + failed) % 100 == 0 and (updated + failed):
            print(f"  …{updated} updated", flush=True)

    print(f"\n=== DONE. updated={updated} already-had-phone={skipped_has} "
          f"no-wc-phone={no_phone} failed={failed} ===", flush=True)


if __name__ == "__main__":
    main()
