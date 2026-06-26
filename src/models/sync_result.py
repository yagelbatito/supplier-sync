"""Result tracking for each sync run."""
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ProductResult:
    sku: str
    name: str
    supplier_url: str
    action: str          # "created" | "updated" | "drafted" | "skipped" | "failed"
    error: str = ""
    requires_review: bool = False
    review_reason: str = ""


@dataclass
class SupplierSyncResult:
    supplier_key: str
    supplier_name: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    total_scraped: int = 0
    created: int = 0
    updated: int = 0
    drafted: int = 0
    out_of_stock: int = 0
    failed: int = 0
    skipped: int = 0
    requires_review: int = 0

    products: list[ProductResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc)

    @property
    def duration_seconds(self) -> float:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return 0.0

    def add_product(self, result: ProductResult) -> None:
        self.products.append(result)
        if result.action == "created":
            self.created += 1
        elif result.action == "updated":
            self.updated += 1
        elif result.action == "drafted":
            self.drafted += 1
        elif result.action == "failed":
            self.failed += 1
            self.errors.append(f"{result.sku}: {result.error}")
        elif result.action == "skipped":
            self.skipped += 1
        if result.requires_review:
            self.requires_review += 1


@dataclass
class RunReport:
    run_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    supplier_results: list[SupplierSyncResult] = field(default_factory=list)

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc)

    @property
    def total_created(self) -> int:
        return sum(r.created for r in self.supplier_results)

    @property
    def total_updated(self) -> int:
        return sum(r.updated for r in self.supplier_results)

    @property
    def total_failed(self) -> int:
        return sum(r.failed for r in self.supplier_results)

    @property
    def total_drafted(self) -> int:
        return sum(r.drafted for r in self.supplier_results)

    @property
    def duration_seconds(self) -> float:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return 0.0
