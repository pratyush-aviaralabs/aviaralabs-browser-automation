"""CRUD for Scheduler + its SchedulerOccurrence rows. Validates
frequency_unit and the day_of_week/day_of_month each occurrence needs
for it, and keeps next_run_at correct on every create/update by
reusing the same cron math the tick process uses.

target_status accepts READY_FOR_PROCESSING, FAILED, and
PROCESSED_BUT_NEED_TO_VALIDATE — the three statuses a dealer actually
wants to periodically retry/pick up. The other Invoice.status values
(RECEIVED, INGESTING, FAILED_DATA_STORAGE, FAILED_FILE_STORAGE,
QUEUED, RUNNING, PROCESSED) are deliberately excluded: the first four
belong to the ingestion pipeline, not the workflow-run one a scheduler
drives, and QUEUED/RUNNING/PROCESSED are in-flight-or-done states that
make no sense as a recurring target."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..config import DBConfig
from ..db.engine import get_session_factory
from ..db.models import Scheduler, SchedulerOccurrence, SchedulerRun, Tenant
from ..ingestion import status as s
from .webhooks import _get_or_create_default_tenant
from ..scheduler.cron import FREQUENCY_UNITS
from ..scheduler.tick import compute_next_run_at
from ..utils.timezones import US_TIMEZONES, is_valid_timezone

router = APIRouter()

_SESSION_FACTORY = get_session_factory(DBConfig())

_ALLOWED_TARGET_STATUSES = {s.READY_FOR_PROCESSING, s.FAILED, s.PROCESSED_BUT_NEED_TO_VALIDATE}


def _validate_occurrence(frequency_unit: str, occurrence: dict) -> None:
    time_of_day = occurrence.get("time_of_day")
    if not time_of_day or len(time_of_day.split(":")) != 2:
        raise HTTPException(422, "each occurrence needs time_of_day as 'HH:MM'")
    if frequency_unit == "WEEK" and occurrence.get("day_of_week") is None:
        raise HTTPException(422, "WEEK occurrences need day_of_week (0=Mon..6=Sun)")
    if frequency_unit == "MONTH" and occurrence.get("day_of_month") is None:
        raise HTTPException(422, "MONTH occurrences need day_of_month (1-31)")


def _validate_scheduler_payload(body: dict) -> None:
    if body.get("frequency_unit") not in FREQUENCY_UNITS:
        raise HTTPException(422, f"frequency_unit must be one of {FREQUENCY_UNITS}")
    if not is_valid_timezone(body.get("timezone", "UTC")):
        raise HTTPException(422, f"timezone {body.get('timezone')!r} is not a recognized IANA timezone")
    if body.get("target_status", s.READY_FOR_PROCESSING) not in _ALLOWED_TARGET_STATUSES:
        raise HTTPException(
            422, f"target_status must be one of {sorted(_ALLOWED_TARGET_STATUSES)} (more coming later)"
        )
    occurrences = body.get("occurrences") or []
    if not occurrences:
        raise HTTPException(422, "at least one occurrence is required")
    for occurrence in occurrences:
        _validate_occurrence(body["frequency_unit"], occurrence)


def _scheduler_to_dict(session, scheduler: Scheduler) -> dict:
    occurrences = session.scalars(
        select(SchedulerOccurrence).where(SchedulerOccurrence.scheduler_id == scheduler.id)
    ).all()
    return {
        "id": scheduler.id,
        "name": scheduler.name,
        "is_active": scheduler.is_active,
        "target_status": scheduler.target_status,
        "frequency_unit": scheduler.frequency_unit,
        "timezone": scheduler.timezone,
        "last_run_at": scheduler.last_run_at.isoformat() if scheduler.last_run_at else None,
        "next_run_at": scheduler.next_run_at.isoformat() if scheduler.next_run_at else None,
        "occurrences": [
            {
                "id": occ.id,
                "time_of_day": occ.time_of_day,
                "day_of_week": occ.day_of_week,
                "day_of_month": occ.day_of_month,
            }
            for occ in occurrences
        ],
    }


def _replace_occurrences(session, scheduler: Scheduler, occurrences: list[dict]) -> None:
    session.query(SchedulerOccurrence).filter(SchedulerOccurrence.scheduler_id == scheduler.id).delete()
    for occ in occurrences:
        session.add(
            SchedulerOccurrence(
                scheduler_id=scheduler.id,
                time_of_day=occ["time_of_day"],
                day_of_week=occ.get("day_of_week"),
                day_of_month=occ.get("day_of_month"),
            )
        )


@router.get("/timezones")
def list_timezones():
    return {"items": [{"value": "UTC", "label": "UTC"}, *US_TIMEZONES]}


@router.get("/schedulers")
def list_schedulers():
    with _SESSION_FACTORY() as session:
        schedulers = session.scalars(select(Scheduler).order_by(Scheduler.id)).all()
        return {"items": [_scheduler_to_dict(session, sc) for sc in schedulers]}


@router.post("/schedulers")
def create_scheduler(body: dict):
    _validate_scheduler_payload(body)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with _SESSION_FACTORY() as session:
        tenant = _get_or_create_default_tenant(session)
        scheduler = Scheduler(
            tenant_id=tenant.id,
            name=body.get("name") or "Untitled scheduler",
            is_active=body.get("is_active", True),
            target_status=body.get("target_status", s.READY_FOR_PROCESSING),
            frequency_unit=body["frequency_unit"],
            timezone=body.get("timezone", "UTC"),
        )
        session.add(scheduler)
        session.flush()

        _replace_occurrences(session, scheduler, body["occurrences"])
        session.flush()

        scheduler.next_run_at = compute_next_run_at(session, scheduler, after=now)
        session.commit()
        return _scheduler_to_dict(session, scheduler)


@router.patch("/schedulers/{scheduler_id}")
def update_scheduler(scheduler_id: int, body: dict):
    with _SESSION_FACTORY() as session:
        scheduler = session.get(Scheduler, scheduler_id)
        if scheduler is None:
            raise HTTPException(404, "scheduler not found")

        merged = {
            "frequency_unit": body.get("frequency_unit", scheduler.frequency_unit),
            "target_status": body.get("target_status", scheduler.target_status),
            "timezone": body.get("timezone", scheduler.timezone),
            "occurrences": body.get(
                "occurrences",
                [
                    {
                        "time_of_day": occ.time_of_day,
                        "day_of_week": occ.day_of_week,
                        "day_of_month": occ.day_of_month,
                    }
                    for occ in session.scalars(
                        select(SchedulerOccurrence).where(SchedulerOccurrence.scheduler_id == scheduler.id)
                    ).all()
                ],
            ),
        }
        _validate_scheduler_payload(merged)

        if "name" in body:
            scheduler.name = body["name"]
        if "is_active" in body:
            scheduler.is_active = body["is_active"]
        scheduler.target_status = merged["target_status"]
        scheduler.frequency_unit = merged["frequency_unit"]
        if "timezone" in body:
            scheduler.timezone = body["timezone"]
        if "occurrences" in body:
            _replace_occurrences(session, scheduler, body["occurrences"])
        session.flush()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        scheduler.next_run_at = compute_next_run_at(session, scheduler, after=now) if scheduler.is_active else None
        session.commit()
        return _scheduler_to_dict(session, scheduler)


@router.delete("/schedulers/{scheduler_id}")
def delete_scheduler(scheduler_id: int):
    with _SESSION_FACTORY() as session:
        scheduler = session.get(Scheduler, scheduler_id)
        if scheduler is None:
            raise HTTPException(404, "scheduler not found")
        session.query(SchedulerRun).filter(SchedulerRun.scheduler_id == scheduler_id).delete()
        session.query(SchedulerOccurrence).filter(SchedulerOccurrence.scheduler_id == scheduler_id).delete()
        session.delete(scheduler)
        session.commit()
        return {"deleted": scheduler_id}


@router.get("/schedulers/{scheduler_id}/runs")
def list_scheduler_runs(scheduler_id: int, limit: int = 20):
    with _SESSION_FACTORY() as session:
        if session.get(Scheduler, scheduler_id) is None:
            raise HTTPException(404, "scheduler not found")
        runs = session.scalars(
            select(SchedulerRun)
            .where(SchedulerRun.scheduler_id == scheduler_id)
            .order_by(SchedulerRun.ran_at.desc())
            .limit(limit)
        ).all()
        return {
            "items": [
                {
                    "id": run.id,
                    "ran_at": run.ran_at.isoformat(),
                    "target_status": run.target_status,
                    "invoice_ids": run.invoice_ids_json,
                }
                for run in runs
            ]
        }
