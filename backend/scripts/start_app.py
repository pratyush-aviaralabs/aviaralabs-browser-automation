"""Single-command dev entrypoint: starts the web app (uvicorn), both
RQ workers (ingestion, workflow-run), and the scheduler tick loop
together, interleaving their output with
[web]/[ingestion-worker]/[workflow-worker]/[scheduler-tick] prefixes in
one terminal, and stops all of them together on Ctrl+C.

Does NOT start Redis itself — that's infra, not app code (see
dc-redis in the project's docker setup). Checks it's reachable first
and fails fast with a clear message instead of starting processes
that will just error out.

    PYTHONPATH=. venv/bin/python scripts/start_app.py
    PYTHONPATH=. venv/bin/python scripts/start_app.py --port 8080
"""
import argparse
import signal
import subprocess
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

from dealercenter_agent.queue.connection import get_redis_connection  # noqa: E402


def _check_redis() -> None:
    try:
        get_redis_connection().ping()
    except Exception as exc:
        print(
            "Redis isn't reachable — start it first, e.g.:\n"
            "  docker run -d --name dc-redis --restart unless-stopped -p 6379:6379 redis:7-alpine\n"
            f"(error: {exc})",
            file=sys.stderr,
        )
        sys.exit(1)


def _stream(process: subprocess.Popen, prefix: str) -> None:
    for line in process.stdout:
        print(f"[{prefix}] {line.rstrip()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="8000")
    args = parser.parse_args()

    _check_redis()

    processes = {
        "web": subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "dealercenter_agent.web.app:app", "--port", args.port],
            cwd=PROJECT_ROOT / "backend",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        ),
        "ingestion-worker": subprocess.Popen(
            [sys.executable, str(Path(__file__).parent / "run_worker.py")],
            cwd=PROJECT_ROOT / "backend",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        ),
        "workflow-worker": subprocess.Popen(
            [sys.executable, str(Path(__file__).parent / "run_workflow_worker.py")],
            cwd=PROJECT_ROOT / "backend",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        ),
        "scheduler-tick": subprocess.Popen(
            [sys.executable, str(Path(__file__).parent / "run_scheduler_tick.py")],
            cwd=PROJECT_ROOT / "backend",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        ),
    }

    threads = [
        threading.Thread(target=_stream, args=(process, name), daemon=True)
        for name, process in processes.items()
    ]
    for t in threads:
        t.start()

    def shutdown(*_args) -> None:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for process in processes.values():
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"[start_app] web on :{args.port}, ingestion + workflow workers + scheduler tick running — Ctrl+C to stop all")

    # If any process dies on its own (crash, not Ctrl+C), bring the
    # others down too rather than leaving a half-running app.
    while True:
        for name, process in processes.items():
            if process.poll() is not None:
                print(f"[start_app] {name} exited ({process.returncode}) — stopping everything")
                shutdown()
        for process in processes.values():
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass


if __name__ == "__main__":
    main()
