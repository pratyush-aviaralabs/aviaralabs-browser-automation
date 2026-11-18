"""Deletes every Invoice row created by a load_test_webhook.py run.

    PYTHONPATH=. venv/bin/python scripts/cleanup_load_test.py --run-tag LOAD-1794971234
    PYTHONPATH=. venv/bin/python scripts/cleanup_load_test.py --all-load-test-rows
"""
import argparse
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

from dealercenter_agent.config import DBConfig  # noqa: E402
from dealercenter_agent.db.engine import get_session_factory  # noqa: E402
from dealercenter_agent.db.models import Invoice  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-tag", help="e.g. LOAD-1794971234, printed at the start of load_test_webhook.py")
    group.add_argument("--all-load-test-rows", action="store_true", help="delete every INV-LOAD-% row, any run")
    args = parser.parse_args()

    pattern = f"INV-{args.run_tag}%" if args.run_tag else "INV-LOAD-%"
    session_factory = get_session_factory(DBConfig())
    with session_factory() as session:
        matches = session.query(Invoice).filter(Invoice.invoice_number.like(pattern))
        count = matches.count()
        matches.delete(synchronize_session=False)
        session.commit()
    print(f"deleted {count} invoice row(s) matching {pattern!r}")
