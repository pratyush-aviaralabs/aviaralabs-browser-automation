"""SQLAlchemy models. Only dialect-agnostic column types are used (String
with explicit lengths, Numeric, Date, DateTime, JSON — never Postgres-only
JSONB) so the same models and Alembic migrations work unmodified against
SQLite (tests), Supabase/Postgres (dev), and MySQL (prod)."""

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    invoice_number: Mapped[str] = mapped_column(String(100), index=True)
    vendor_name: Mapped[str] = mapped_column(String(255))
    grand_total: Mapped[float] = mapped_column(Numeric(10, 2))
    invoice_date: Mapped[date] = mapped_column(Date)
    # Full VIN as received from ingestion — truncation to last-6 for
    # DealerCenter's search box is a SearchInvoiceTool-time concern, not
    # a storage decision.
    vin: Mapped[str | None] = mapped_column(String(17), nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stock_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Widened to 40 — confirmed required (2026-11-18): the ingestion/
    # workflow-engine status vocabulary includes values up to
    # PROCESSED_BUT_NEED_TO_VALIDATE (30 chars), well past the original
    # String(20). See ingestion/status.py for the full vocabulary.
    status: Mapped[str] = mapped_column(String(40), default="QUEUED")
    # The upstream system's own id for this invoice (a Mongo ObjectId in
    # the webhook ingestion case). NOT unique — confirmed required
    # (2026-11-18): duplicate invoices are accepted deliberately, so
    # the same external_invoice_id can appear on more than one row.
    external_invoice_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    # The trailing path segment of callback_url, extracted once at
    # ingestion time so the status-check endpoint can look a row up by
    # token directly instead of string-matching callback_url every time.
    callback_token: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    local_pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    callback_url: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class Workflow(Base):
    """A named, versioned tool sequence. The sequence itself lives in
    WorkflowStep rows, not in code — reordering/swapping a step later is
    a data edit, which is what makes this editable from a future UI."""

    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (UniqueConstraint("workflow_id", "step_order"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"))
    step_order: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(100))
    config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class WorkflowRun(Base):
    """One per invoice's trip through a Workflow. current_step_order is
    the retry target a future UI points a 'retry this step' action at —
    resuming from here, not from the start of the whole pipeline."""

    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    current_step_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ToolRun(Base):
    """One row per tool call — the cost/time ledger. invoice_id is
    denormalized (also reachable via workflow_run_id -> invoice_id) so
    'all tool calls for invoice X' stays a single-table query."""

    __tablename__ = "tool_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    workflow_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_runs.id"), nullable=True
    )
    workflow_step_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_steps.id"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(100))
    input_json: Mapped[dict] = mapped_column(JSON, default=dict)
    output_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20))
    duration_ms: Mapped[int] = mapped_column(Integer)
    llm_cost_usd: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RecoveryAttempt(Base):
    """Phase 3 (LangGraph recovery layer) writes here; the table exists
    now so this migration is the only schema change it ever needs."""

    __tablename__ = "recovery_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    tool_run_id: Mapped[int] = mapped_column(ForeignKey("tool_runs.id"))
    classification: Mapped[str | None] = mapped_column(String(100), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    action_taken: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Join key a future FE uses to deep-link into LangSmith.
    langsmith_run_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    workflow_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_runs.id"), nullable=True
    )
    reason: Mapped[str] = mapped_column(String(500))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class AuthSession(Base):
    """Tenant-scoped browser session persistence — the DB-backed
    successor to the flat .auth_state.json file used in the POC."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    storage_state_json: Mapped[dict] = mapped_column(JSON)
    device_trusted: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Scheduler(Base):
    """One recurring rule picking up invoices in a given status and
    queueing them for the workflow run. Multiple can be active at
    once, independently. target_status is a plain string, not an
    enum/FK on purpose — confirmed as a deliberate "option" (2026-11-18):
    expanding to FAILED / PROCESSED_BUT_NEED_TO_VALIDATE later is then a
    pure validation-layer change, zero migration needed."""

    __tablename__ = "schedulers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    target_status: Mapped[str] = mapped_column(String(40), default="READY_FOR_PROCESSING")
    frequency_unit: Mapped[str] = mapped_column(String(10))  # HOUR | DAY | WEEK | MONTH
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Precomputed earliest upcoming fire time across this scheduler's
    # occurrences — lets the tick loop query
    # "WHERE is_active AND next_run_at <= now()" instead of recomputing
    # cron math for every scheduler on every tick.
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class SchedulerOccurrence(Base):
    """One fire-point belonging to a Scheduler. The COUNT of these rows
    for a given scheduler_id is "once/twice/thrice" — no separate
    counter column to keep in sync with the actual rows."""

    __tablename__ = "scheduler_occurrences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scheduler_id: Mapped[int] = mapped_column(ForeignKey("schedulers.id"))
    time_of_day: Mapped[str] = mapped_column(String(5))  # "HH:MM", 24h — for HOUR freq, only MM is used
    # Only meaningful (and set) when frequency_unit == WEEK.
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0=Mon .. 6=Sun
    # Only meaningful (and set) when frequency_unit == MONTH.
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-31
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class SchedulerRun(Base):
    """Execution log — one row per time a scheduler actually fired.
    Same auditable-history pattern as WorkflowRun/ToolRun."""

    __tablename__ = "scheduler_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scheduler_id: Mapped[int] = mapped_column(ForeignKey("schedulers.id"))
    ran_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    target_status: Mapped[str] = mapped_column(String(40))  # snapshot, in case it changes later
    invoice_ids_json: Mapped[list] = mapped_column(JSON, default=list)
