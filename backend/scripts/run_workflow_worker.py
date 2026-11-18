"""RQ worker for the workflow_run queue — processes queued invoices
through the CostAdd pipeline one at a time, headless. Separate process
(and separate queue) from the ingestion worker on purpose: a multi-
minute browser automation run must never block fast ingestion jobs.

    PYTHONPATH=. venv/bin/python scripts/run_workflow_worker.py
"""
from pathlib import Path

from dotenv import load_dotenv
from rq import SimpleWorker

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from dealercenter_agent.queue.connection import get_workflow_run_queue  # noqa: E402

if __name__ == "__main__":
    queue = get_workflow_run_queue()
    SimpleWorker([queue], connection=queue.connection).work()
