"""Periodic scheduler tick — wakes up once a minute, fires any due
Scheduler rows (see dealercenter_agent/scheduler/tick.py). Not an RQ
worker; this is a plain polling loop, since "is anything due" is a
cheap DB query, not work worth queueing itself.

    PYTHONPATH=. venv/bin/python scripts/run_scheduler_tick.py
"""
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from dealercenter_agent.scheduler.tick import run_due_schedulers  # noqa: E402

TICK_INTERVAL_SECONDS = 60

if __name__ == "__main__":
    print(f"scheduler tick loop started, checking every {TICK_INTERVAL_SECONDS}s")
    while True:
        fired = run_due_schedulers()
        if fired:
            print(f"fired schedulers: {fired}")
        time.sleep(TICK_INTERVAL_SECONDS)
