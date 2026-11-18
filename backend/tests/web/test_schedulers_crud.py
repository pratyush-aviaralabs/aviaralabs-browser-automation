import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from dealercenter_agent.db.models import Base
from dealercenter_agent.web import app as app_module
from dealercenter_agent.web import schedulers as schedulers_module


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(schedulers_module, "_SESSION_FACTORY", session_factory)
    return TestClient(app_module.app)


DAILY_PAYLOAD = {
    "name": "Hourly sweep",
    "frequency_unit": "DAY",
    "occurrences": [{"time_of_day": "09:00"}, {"time_of_day": "17:00"}],
}


def test_create_scheduler_computes_next_run_at(client):
    resp = client.post("/schedulers", json=DAILY_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Hourly sweep"
    assert body["is_active"] is True
    assert body["target_status"] == "READY_FOR_PROCESSING"
    assert body["next_run_at"] is not None
    assert len(body["occurrences"]) == 2


def test_create_scheduler_rejects_unknown_frequency_unit(client):
    resp = client.post("/schedulers", json={**DAILY_PAYLOAD, "frequency_unit": "YEAR"})
    assert resp.status_code == 422


def test_create_scheduler_rejects_disallowed_target_status(client):
    resp = client.post("/schedulers", json={**DAILY_PAYLOAD, "target_status": "QUEUED"})
    assert resp.status_code == 422


@pytest.mark.parametrize("target_status", ["FAILED", "PROCESSED_BUT_NEED_TO_VALIDATE"])
def test_create_scheduler_accepts_other_target_statuses(client, target_status):
    resp = client.post("/schedulers", json={**DAILY_PAYLOAD, "target_status": target_status})
    assert resp.status_code == 200
    assert resp.json()["target_status"] == target_status


def test_create_scheduler_rejects_empty_occurrences(client):
    resp = client.post("/schedulers", json={**DAILY_PAYLOAD, "occurrences": []})
    assert resp.status_code == 422


def test_create_weekly_scheduler_requires_day_of_week(client):
    resp = client.post(
        "/schedulers",
        json={"name": "Weekly", "frequency_unit": "WEEK", "occurrences": [{"time_of_day": "08:00"}]},
    )
    assert resp.status_code == 422


def test_create_monthly_scheduler_requires_day_of_month(client):
    resp = client.post(
        "/schedulers",
        json={"name": "Monthly", "frequency_unit": "MONTH", "occurrences": [{"time_of_day": "08:00"}]},
    )
    assert resp.status_code == 422


def test_list_schedulers_returns_created_ones(client):
    client.post("/schedulers", json=DAILY_PAYLOAD)
    resp = client.get("/schedulers")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


def test_update_scheduler_can_toggle_active(client):
    created = client.post("/schedulers", json=DAILY_PAYLOAD).json()
    resp = client.patch(f"/schedulers/{created['id']}", json={"is_active": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_active"] is False
    assert body["next_run_at"] is None  # paused schedulers don't have a pending next run


def test_update_scheduler_can_replace_occurrences(client):
    created = client.post("/schedulers", json=DAILY_PAYLOAD).json()
    resp = client.patch(
        f"/schedulers/{created['id']}",
        json={"occurrences": [{"time_of_day": "12:00"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["occurrences"]) == 1
    assert body["occurrences"][0]["time_of_day"] == "12:00"


def test_update_unknown_scheduler_404s(client):
    resp = client.patch("/schedulers/999999", json={"is_active": False})
    assert resp.status_code == 404


def test_delete_scheduler_removes_it_and_its_occurrences(client):
    created = client.post("/schedulers", json=DAILY_PAYLOAD).json()
    resp = client.delete(f"/schedulers/{created['id']}")
    assert resp.status_code == 200
    assert client.get("/schedulers").json()["items"] == []


def test_delete_unknown_scheduler_404s(client):
    resp = client.delete("/schedulers/999999")
    assert resp.status_code == 404


def test_scheduler_runs_endpoint_starts_empty(client):
    created = client.post("/schedulers", json=DAILY_PAYLOAD).json()
    resp = client.get(f"/schedulers/{created['id']}/runs")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_scheduler_runs_endpoint_404s_for_unknown_scheduler(client):
    resp = client.get("/schedulers/999999/runs")
    assert resp.status_code == 404


def test_create_scheduler_rejects_unknown_timezone(client):
    resp = client.post("/schedulers", json={**DAILY_PAYLOAD, "timezone": "Not/AZone"})
    assert resp.status_code == 422


def test_create_scheduler_accepts_valid_us_timezone(client):
    resp = client.post("/schedulers", json={**DAILY_PAYLOAD, "timezone": "America/Chicago"})
    assert resp.status_code == 200
    assert resp.json()["timezone"] == "America/Chicago"


def test_update_scheduler_rejects_unknown_timezone(client):
    created = client.post("/schedulers", json=DAILY_PAYLOAD).json()
    resp = client.patch(f"/schedulers/{created['id']}", json={"timezone": "Not/AZone"})
    assert resp.status_code == 422


def test_timezones_endpoint_lists_utc_first_then_us_zones(client):
    resp = client.get("/timezones")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items[0] == {"value": "UTC", "label": "UTC"}
    values = {tz["value"] for tz in items}
    assert "America/New_York" in values
    assert "America/Los_Angeles" in values
