from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from dealercenter_agent.db.models import (
    Base,
    Invoice,
    Scheduler,
    SchedulerOccurrence,
    SchedulerRun,
    Tenant,
)
from dealercenter_agent.ingestion import status as s
from dealercenter_agent.scheduler import tick


class FakeQueue:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, func, *args, **kwargs):
        self.enqueued.append((func, args, kwargs))


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(tick, "_SESSION_FACTORY", session_factory)

    fake_queue = FakeQueue()
    monkeypatch.setattr(tick, "get_workflow_run_queue", lambda: fake_queue)

    with session_factory() as session:
        session.add(Tenant(name="Dev Dealer"))
        session.commit()

    return session_factory, fake_queue


def _make_scheduler(session_factory, next_run_at, is_active=True, target_status=s.READY_FOR_PROCESSING):
    with session_factory() as session:
        tenant = session.query(Tenant).first()
        scheduler = Scheduler(
            tenant_id=tenant.id,
            name="Test scheduler",
            is_active=is_active,
            target_status=target_status,
            frequency_unit="DAY",
            timezone="UTC",
            next_run_at=next_run_at,
        )
        session.add(scheduler)
        session.flush()
        session.add(SchedulerOccurrence(scheduler_id=scheduler.id, time_of_day="09:00"))
        session.commit()
        return scheduler.id


def _make_invoice(session_factory, status) -> int:
    with session_factory() as session:
        tenant = session.query(Tenant).first()
        invoice = Invoice(
            tenant_id=tenant.id,
            invoice_number="INV-1",
            vendor_name="Vendor",
            grand_total=1,
            invoice_date=date(2026, 11, 18),
            status=status,
            callback_url="https://example.com/callback/",
        )
        session.add(invoice)
        session.commit()
        return invoice.id


def test_due_scheduler_queues_matching_invoices_and_logs_a_run(db):
    session_factory, fake_queue = db
    invoice_id = _make_invoice(session_factory, s.READY_FOR_PROCESSING)
    scheduler_id = _make_scheduler(session_factory, next_run_at=datetime(2026, 11, 18, 9, 0))

    fired = tick.run_due_schedulers(now=datetime(2026, 11, 18, 9, 0, 30))

    assert fired == [scheduler_id]
    with session_factory() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.QUEUED
        runs = session.scalars(select(SchedulerRun).where(SchedulerRun.scheduler_id == scheduler_id)).all()
        assert len(runs) == 1
        assert runs[0].invoice_ids_json == [invoice_id]
        scheduler = session.get(Scheduler, scheduler_id)
        assert scheduler.last_run_at == datetime(2026, 11, 18, 9, 0, 30)
        # Daily at 09:00, just ran -> next is tomorrow 09:00.
        assert scheduler.next_run_at == datetime(2026, 11, 19, 9, 0)

    assert len(fake_queue.enqueued) == 1
    func, args, _kwargs = fake_queue.enqueued[0]
    assert func.__name__ == "process_workflow_run"
    assert args == (invoice_id,)


def test_inactive_scheduler_never_fires(db):
    session_factory, fake_queue = db
    _make_invoice(session_factory, s.READY_FOR_PROCESSING)
    _make_scheduler(session_factory, next_run_at=datetime(2026, 11, 18, 9, 0), is_active=False)

    fired = tick.run_due_schedulers(now=datetime(2026, 11, 18, 9, 0, 30))

    assert fired == []
    assert len(fake_queue.enqueued) == 0


def test_scheduler_not_yet_due_does_not_fire(db):
    session_factory, fake_queue = db
    _make_invoice(session_factory, s.READY_FOR_PROCESSING)
    _make_scheduler(session_factory, next_run_at=datetime(2026, 11, 20, 9, 0))

    fired = tick.run_due_schedulers(now=datetime(2026, 11, 18, 9, 0, 30))

    assert fired == []
    assert len(fake_queue.enqueued) == 0


def test_scheduler_only_queues_invoices_matching_its_target_status(db):
    session_factory, fake_queue = db
    _make_invoice(session_factory, s.FAILED_FILE_STORAGE)
    ready_id = _make_invoice(session_factory, s.READY_FOR_PROCESSING)
    _make_scheduler(session_factory, next_run_at=datetime(2026, 11, 18, 9, 0))

    tick.run_due_schedulers(now=datetime(2026, 11, 18, 9, 0, 30))

    assert len(fake_queue.enqueued) == 1
    assert fake_queue.enqueued[0][1] == (ready_id,)


def test_two_schedulers_sharing_a_target_status_do_not_double_queue_the_same_invoice(db):
    session_factory, fake_queue = db
    invoice_id = _make_invoice(session_factory, s.READY_FOR_PROCESSING)
    first_id = _make_scheduler(session_factory, next_run_at=datetime(2026, 11, 18, 9, 0))
    second_id = _make_scheduler(session_factory, next_run_at=datetime(2026, 11, 18, 9, 0))

    fired = tick.run_due_schedulers(now=datetime(2026, 11, 18, 9, 0, 30))

    assert set(fired) == {first_id, second_id}
    assert len(fake_queue.enqueued) == 1
    assert fake_queue.enqueued[0][1] == (invoice_id,)
    with session_factory() as session:
        runs = session.scalars(select(SchedulerRun)).all()
        assert sorted(run.invoice_ids_json for run in runs) == [[], [invoice_id]]


def test_rerunning_tick_with_the_same_now_does_not_refire_an_already_handled_scheduler(db):
    session_factory, fake_queue = db
    invoice_id = _make_invoice(session_factory, s.READY_FOR_PROCESSING)
    scheduler_id = _make_scheduler(session_factory, next_run_at=datetime(2026, 11, 18, 9, 0))
    now = datetime(2026, 11, 18, 9, 0, 30)

    first_fired = tick.run_due_schedulers(now=now)
    second_fired = tick.run_due_schedulers(now=now)

    assert first_fired == [scheduler_id]
    assert second_fired == []
    assert len(fake_queue.enqueued) == 1
    with session_factory() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.QUEUED
        runs = session.scalars(select(SchedulerRun).where(SchedulerRun.scheduler_id == scheduler_id)).all()
        assert len(runs) == 1


def test_scheduler_with_no_matching_invoices_still_fires_and_reschedules(db):
    session_factory, fake_queue = db
    scheduler_id = _make_scheduler(session_factory, next_run_at=datetime(2026, 11, 18, 9, 0))

    fired = tick.run_due_schedulers(now=datetime(2026, 11, 18, 9, 0, 30))

    assert fired == [scheduler_id]
    assert len(fake_queue.enqueued) == 0
    with session_factory() as session:
        scheduler = session.get(Scheduler, scheduler_id)
        assert scheduler.next_run_at == datetime(2026, 11, 19, 9, 0)
