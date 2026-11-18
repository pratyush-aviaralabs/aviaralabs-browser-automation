"""Lazy Redis/RQ wiring. Redis.from_url() and Queue() don't actually open
a connection until the first command runs, so importing this module (and
building the module-level queue below) is safe even with no Redis
reachable yet — same lazy-connect property SQLAlchemy's create_engine
already relies on elsewhere in this app."""

from redis import Redis
from rq import Queue

from ..config import QueueConfig

INVOICE_INGESTION_QUEUE_NAME = "invoice_ingestion"
# Separate from ingestion on purpose — ingestion jobs are seconds,
# workflow-run jobs are minutes (a full browser automation pass); on
# one shared queue a slow workflow run would starve fast ingestion
# jobs behind it.
WORKFLOW_RUN_QUEUE_NAME = "workflow_run"

_redis_conn: Redis | None = None
_invoice_ingestion_queue: Queue | None = None
_workflow_run_queue: Queue | None = None


def get_redis_connection() -> Redis:
    global _redis_conn
    if _redis_conn is None:
        _redis_conn = Redis.from_url(QueueConfig().redis_url)
    return _redis_conn


def get_invoice_ingestion_queue() -> Queue:
    global _invoice_ingestion_queue
    if _invoice_ingestion_queue is None:
        _invoice_ingestion_queue = Queue(INVOICE_INGESTION_QUEUE_NAME, connection=get_redis_connection())
    return _invoice_ingestion_queue


def get_workflow_run_queue() -> Queue:
    global _workflow_run_queue
    if _workflow_run_queue is None:
        _workflow_run_queue = Queue(WORKFLOW_RUN_QUEUE_NAME, connection=get_redis_connection())
    return _workflow_run_queue
