"""RQ worker entrypoint for invoice ingestion. Run one of these per
worker process you want; at 500-600 invoices/day, one process is plenty,
but this scales horizontally by just starting more.

Uses SimpleWorker, not the default Worker — confirmed required
(2026-11-18), found via a 500-request burst load test: the default
Worker forks a fresh OS process per job for crash isolation, and
SQLAlchemy's connection pool deliberately invalidates pooled connections
when it detects the PID changed (an anti-fork-corruption safeguard),
so every job paid a full cold TCP+TLS reconnect to Supabase (~4s) no
matter what — module-level engine reuse in ingestion/jobs.py had no
effect under the forking worker. SimpleWorker runs jobs in the same
process without forking, so that reuse actually takes effect. Trade-off:
a crashing job can take the worker down with it instead of being
isolated — acceptable here since jobs.py already catches its own
exceptions and marks the invoice failed rather than raising.

    PYTHONPATH=. venv/bin/python scripts/run_worker.py
"""
from pathlib import Path

from dotenv import load_dotenv
from rq import SimpleWorker

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from dealercenter_agent.queue.connection import get_invoice_ingestion_queue  # noqa: E402

if __name__ == "__main__":
    queue = get_invoice_ingestion_queue()
    SimpleWorker([queue], connection=queue.connection).work()
