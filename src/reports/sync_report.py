"""
Sync report generation and writing.
"""
import json
from pathlib import Path

from src.core.logger import get_logger
from src.models.sync_result import RunReport, SupplierSyncResult

logger = get_logger(__name__)

REPORTS_DIR = Path(__file__).resolve().parents[2] / "data" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def print_supplier_summary(result: SupplierSyncResult) -> None:
    lines = [
        f"\n{'='*55}",
        f"  Supplier: {result.supplier_name}",
        f"{'='*55}",
        f"  Scraped:        {result.total_scraped}",
        f"  Created:        {result.created}",
        f"  Updated:        {result.updated}",
        f"  Drafted:        {result.drafted}",
        f"  Out of stock:   {result.out_of_stock}",
        f"  Requires review:{result.requires_review}",
        f"  Failed:         {result.failed}",
        f"  Skipped:        {result.skipped}",
        f"  Duration:       {result.duration_seconds:.1f}s",
    ]
    if result.errors:
        lines.append(f"\n  Errors ({len(result.errors)}):")
        for err in result.errors[:10]:
            lines.append(f"    • {err}")
    print("\n".join(lines))


def print_run_summary(report: RunReport) -> None:
    lines = [
        f"\n{'#'*55}",
        f"  FULL RUN SUMMARY — {report.run_id}",
        f"{'#'*55}",
        f"  Suppliers run:  {len(report.supplier_results)}",
        f"  Total created:  {report.total_created}",
        f"  Total updated:  {report.total_updated}",
        f"  Total drafted:  {report.total_drafted}",
        f"  Total failed:   {report.total_failed}",
        f"  Duration:       {report.duration_seconds:.1f}s",
        f"{'#'*55}\n",
    ]
    print("\n".join(lines))


def save_report(report: RunReport) -> Path:
    """Save full JSON report to data/reports/."""
    filename = f"sync_{report.run_id}.json"
    path = REPORTS_DIR / filename

    data = {
        "run_id": report.run_id,
        "started_at": report.started_at.isoformat(),
        "finished_at": report.finished_at.isoformat() if report.finished_at else None,
        "duration_seconds": report.duration_seconds,
        "totals": {
            "created": report.total_created,
            "updated": report.total_updated,
            "drafted": report.total_drafted,
            "failed": report.total_failed,
        },
        "suppliers": [
            {
                "key": r.supplier_key,
                "name": r.supplier_name,
                "scraped": r.total_scraped,
                "created": r.created,
                "updated": r.updated,
                "drafted": r.drafted,
                "out_of_stock": r.out_of_stock,
                "failed": r.failed,
                "requires_review": r.requires_review,
                "errors": r.errors,
                "duration_seconds": r.duration_seconds,
            }
            for r in report.supplier_results
        ],
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"Report saved: {path}")
    return path
