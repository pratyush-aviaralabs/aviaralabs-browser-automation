"""Scheduler tick: finds Scheduler rows due to fire, enqueues matching
invoices into the workflow_run queue — the exact same path
/invoices/bulk-run uses, so there's one source of truth for "how an
invoice gets queued," not two — logs a SchedulerRun, and recomputes
next_run_at from the scheduler's occurrences."""

from datetime import datetime, timezone

from sqlalchemy import select

from ..config import DBConfig
from ..db.engine import get_session_factory
from ..db.models import Invoice, Scheduler, SchedulerOccurrence, SchedulerRun
from ..ingestion import status as s
from ..queue.connection import get_workflow_run_queue
from ..workflow.jobs import process_workflow_run
from .cron import next_fire_time

_SESSION_FACTORY = get_session_factory(DBConfig())


def compute_next_run_at(session, scheduler: Scheduler, after: datetime) -> datetime | None:
    occurrences = session.scalars(
        select(SchedulerOccurrence).where(SchedulerOccurrence.scheduler_id == scheduler.id)
    ).all()
    if not occurrences:
        return None
    candidates = [
        next_fire_time(
            scheduler.frequency_unit,
            occ.time_of_day,
            occ.day_of_week,
            occ.day_of_month,
            after=after,
            tz_name=scheduler.timezone,
        )
        for occ in occurrences
    ]
    return min(candidates)


def run_due_schedulers(now: datetime | None = None) -> list[int]:
    """Returns the ids of schedulers that fired this tick. Safe to call
    repeatedly/frequently, and safe to run from more than one process at
    once: each due scheduler is claimed individually with
    SELECT ... FOR UPDATE SKIP LOCKED, re-checking is_active/next_run_at
    at claim time (not just at the initial scan) — so if two processes
    race the same due moment, only one of them actually claims and
    processes a given scheduler; the other sees it locked, skips it, and
    moves on rather than blocking or double-firing. The matching
    invoices are locked the same way, so two schedulers that happen to
    share a target_status can't both grab the same invoice. (On SQLite,
    used in tests/dev, FOR UPDATE is a silent no-op — fine, since
    nothing there runs concurrently.)"""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)

    with _SESSION_FACTORY() as session:
        due_ids = session.scalars(
            select(Scheduler.id).where(Scheduler.is_active.is_(True), Scheduler.next_run_at <= now)
        ).all()

    fired_ids = []
    for scheduler_id in due_ids:
        with _SESSION_FACTORY() as session:
            scheduler = session.scalars(
                select(Scheduler)
                .where(
                    Scheduler.id == scheduler_id,
                    Scheduler.is_active.is_(True),
                    Scheduler.next_run_at <= now,
                )
                .with_for_update(skip_locked=True)
            ).first()
            if scheduler is None:
                # Already claimed by another process, deactivated, or
                # rescheduled since the scan above — nothing to do.
                continue

            invoices = session.scalars(
                select(Invoice).where(Invoice.status == scheduler.target_status).with_for_update(skip_locked=True)
            ).all()

            queued_ids = [invoice.id for invoice in invoices]
            for invoice in invoices:
                invoice.status = s.QUEUED

            session.add(
                SchedulerRun(
                    scheduler_id=scheduler.id,
                    ran_at=now,
                    target_status=scheduler.target_status,
                    invoice_ids_json=queued_ids,
                )
            )
            scheduler.last_run_at = now
            scheduler.next_run_at = compute_next_run_at(session, scheduler, after=now)
            session.commit()

        queue = get_workflow_run_queue()
        for invoice_id in queued_ids:
            queue.enqueue(process_workflow_run, invoice_id)

        fired_ids.append(scheduler_id)

    return fired_ids
