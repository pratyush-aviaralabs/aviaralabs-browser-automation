"""Fires N webhook deliveries at /webhooks/invoices back-to-back and
then watches the Redis/RQ queue drain — a direct measurement of how the
ingestion pipeline behaves at volume (e.g. a 500/day burst) instead of
one invoice at a time.

    PYTHONPATH=. venv/bin/python scripts/load_test_webhook.py \\
        --webhook-url http://localhost:8080/webhooks/invoices \\
        --file-url http://localhost:8744/fake-invoice.pdf \\
        --count 500 --concurrency 20

Every invoice this creates is tagged INV-LOAD-<timestamp>-NNNN and a
matching external_invoice_id prefix, so scripts/cleanup_load_test.py can
find and remove them afterward without touching real data.
"""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

from dealercenter_agent.config import DBConfig  # noqa: E402
from dealercenter_agent.db.engine import get_session_factory  # noqa: E402
from dealercenter_agent.db.models import Invoice  # noqa: E402
from dealercenter_agent.queue.connection import get_invoice_ingestion_queue  # noqa: E402

RUN_TAG = f"LOAD-{int(time.time())}"


def build_payload(i: int, file_url: str) -> dict:
    uid = f"{RUN_TAG}-{i:04d}"
    return {
        "data": {
            "document_type": "invoice",
            "invoice_number": f"INV-{uid}",
            "invoice_date": "2026-11-18",
            "po_number": None,
            "vendor_name": "Load Test Vendor",
            "stock_number": str(10000 + i),
            "vin": None,
            "line_items": [{"description": "Load test repair", "quantity": 1, "unit_price": 1, "total": 1}],
            "grand_total": 1.0,
        },
        "file_url": file_url,
        "invoice_id": uid,
        "callback_url": f"http://localhost/organization/connectors/dealercenter/callback/{uid}/",
    }


def send_one(client: httpx.Client, webhook_url: str, payload: dict) -> tuple[int, float]:
    t0 = time.monotonic()
    try:
        resp = client.post(webhook_url, json=payload, timeout=10.0)
        return resp.status_code, time.monotonic() - t0
    except httpx.HTTPError as exc:
        print(f"  request error: {exc}")
        return 0, time.monotonic() - t0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--webhook-url", default="http://localhost:8080/webhooks/invoices")
    parser.add_argument("--file-url", required=True, help="URL the ingestion job will download for every invoice")
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=20, help="simultaneous webhook POSTs")
    args = parser.parse_args()

    print(f"Run tag: {RUN_TAG}")
    payloads = [build_payload(i, args.file_url) for i in range(args.count)]

    print(f"Firing {args.count} webhook deliveries at concurrency={args.concurrency}...")
    t_fire_start = time.monotonic()
    statuses: list[int] = []
    with httpx.Client() as client:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [pool.submit(send_one, client, args.webhook_url, p) for p in payloads]
            for f in as_completed(futures):
                status, _dt = f.result()
                statuses.append(status)
    t_fire_end = time.monotonic()
    fire_elapsed = t_fire_end - t_fire_start

    accepted = statuses.count(200)
    print(
        f"Fired {len(statuses)} requests in {fire_elapsed:.1f}s "
        f"({len(statuses) / fire_elapsed:.1f} req/s) — {accepted} accepted, "
        f"{len(statuses) - accepted} non-200/error"
    )

    queue = get_invoice_ingestion_queue()
    session_factory = get_session_factory(DBConfig())

    print("\nWatching the queue drain (polling every 2s)...")
    t_drain_start = time.monotonic()
    while True:
        queue_depth = queue.count
        with session_factory() as session:
            matches = session.query(Invoice).filter(Invoice.invoice_number.like(f"INV-{RUN_TAG}%"))
            total = matches.count()
            ready = matches.filter(Invoice.status == "ready").count()
            failed = matches.filter(Invoice.status == "failed").count()
        done = ready + failed
        in_flight = total - done
        elapsed = time.monotonic() - t_drain_start
        print(
            f"  t={elapsed:6.1f}s  queue_depth={queue_depth:4d}  "
            f"ready={ready:4d}  failed={failed:4d}  in_flight={in_flight:4d}  done={done}/{args.count}"
        )
        if done >= args.count:
            break
        time.sleep(2)
    t_drain_end = time.monotonic()

    drain_elapsed = t_drain_end - t_drain_start
    total_elapsed = t_drain_end - t_fire_start
    print(
        f"\nDone. {args.count} invoices fully processed.\n"
        f"  Fire time (POST all webhooks):  {fire_elapsed:.1f}s\n"
        f"  Drain time (queue -> ready/failed): {drain_elapsed:.1f}s\n"
        f"  Total wall time:                 {total_elapsed:.1f}s\n"
        f"  Throughput while draining:       {args.count / drain_elapsed:.2f} jobs/sec "
        f"({drain_elapsed / args.count:.2f}s/job average)\n"
        f"\nClean up with:\n  PYTHONPATH=. venv/bin/python scripts/cleanup_load_test.py --run-tag {RUN_TAG}"
    )


if __name__ == "__main__":
    main()
