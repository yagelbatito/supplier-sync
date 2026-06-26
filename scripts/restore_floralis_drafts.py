"""
Re-publish the Floralis products that were wrongly drafted on 2026-05-19
because the supplier's /products.json page 2 returned 403 Forbidden,
making the "disappeared products" check think page-2 items had vanished.

Reads the saved ID list from data/floralis_drafted_due_to_incomplete_scrape_20260519.txt.
For each ID, verifies the product is currently `draft` AND managed by floralis,
then PUTs status=publish via the WC REST API. Already-publish products are skipped.

Usage:
    python -m scripts.restore_floralis_drafts          # dry-run
    python -m scripts.restore_floralis_drafts --apply  # actually write
"""
import argparse
import sys
from pathlib import Path

from src.core.config_loader import load_app_settings, load_env
from src.core.constants import META_SUPPLIER_NAME
from src.core.logger import get_logger
from src.woocommerce.client import WooCommerceClient

logger = get_logger("restore_floralis")

ID_FILE = Path(__file__).resolve().parents[1] / "data" / "floralis_drafted_due_to_incomplete_scrape_20260519.txt"


def load_ids(path: Path) -> list[int]:
    ids: list[int] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("ID="):
            line = line[3:]
        try:
            ids.append(int(line))
        except ValueError:
            logger.warning(f"Skipping unparseable line: {line!r}")
    return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry-run)")
    args = ap.parse_args()

    load_env()
    settings = load_app_settings()
    wc = WooCommerceClient(
        url=settings.woocommerce_url,
        consumer_key=settings.woocommerce_key,
        consumer_secret=settings.woocommerce_secret,
        wp_user=settings.wp_user,
        wp_app_password=settings.wp_app_password,
        verify_ssl=settings.verify_ssl,
        dry_run=False,
    )

    ids = load_ids(ID_FILE)
    logger.info(f"Loaded {len(ids)} candidate IDs from {ID_FILE.name}")
    if not ids:
        return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    logger.info(f"Mode: {mode}")

    restored = 0
    skipped_not_draft = 0
    skipped_wrong_supplier = 0
    not_found = 0
    failed = 0

    for wc_id in ids:
        try:
            product = wc.get(f"products/{wc_id}")
        except Exception as exc:
            not_found += 1
            logger.warning(f"ID={wc_id}: lookup failed: {exc}")
            continue

        status = product.get("status")
        meta = {m["key"]: m["value"] for m in product.get("meta_data", [])}
        supplier = (meta.get(META_SUPPLIER_NAME) or "").lower()
        name = product.get("name", "")

        if supplier != "floralis":
            skipped_wrong_supplier += 1
            logger.warning(f"ID={wc_id}: supplier='{supplier}' (not floralis) — skipping for safety")
            continue

        if status != "draft":
            skipped_not_draft += 1
            logger.info(f"ID={wc_id}: already status='{status}' — nothing to do")
            continue

        if not args.apply:
            logger.info(f"[DRY-RUN] would restore ID={wc_id} '{name[:60]}'")
            restored += 1
            continue

        try:
            wc.put(f"products/{wc_id}", {"status": "publish"})
            restored += 1
            logger.info(f"Restored ID={wc_id} '{name[:60]}'")
        except Exception as exc:
            failed += 1
            logger.error(f"ID={wc_id}: PUT failed: {exc}")

    logger.info("─" * 55)
    logger.info(f"Restored:               {restored}")
    logger.info(f"Skipped (not draft):    {skipped_not_draft}")
    logger.info(f"Skipped (not floralis): {skipped_wrong_supplier}")
    logger.info(f"Lookup failed:          {not_found}")
    logger.info(f"PUT failed:             {failed}")
    if not args.apply:
        logger.info("This was a DRY-RUN. Re-run with --apply to actually write.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
