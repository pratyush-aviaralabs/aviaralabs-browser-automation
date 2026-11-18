"""Invoice.status vocabulary. Confirmed required (2026-11-18).

Ingestion phase (implemented — webhooks.py + ingestion/jobs.py):
  RECEIVED    -> row created from the webhook, ingestion job not yet run
  INGESTING   -> job picked it up, downloading now
  READY_FOR_PROCESSING -> ingestion succeeded: invoice data stored AND
                          its file downloaded — ready for the workflow
                          engine to pick up
  FAILED_DATA_STORAGE  -> ingestion failed persisting invoice data
  FAILED_FILE_STORAGE  -> ingestion failed downloading/storing the file
                          (file_url and every attachment fallback failed)

Workflow-engine phase (NOT implemented yet — defined now so the
vocabulary is settled once, not reinvented per-caller when that phase
gets built):
  QUEUED      -> the workflow engine will process it
  RUNNING     -> a workflow run was created and is currently executing
  PROCESSED   -> the workflow run completed and DealerCenter was updated
  PROCESSED_BUT_NEED_TO_VALIDATE -> workflow steps ran but a check/
                                    validate step failed
  FAILED      -> workflow run failed
"""

# Ingestion phase
RECEIVED = "RECEIVED"
INGESTING = "INGESTING"
READY_FOR_PROCESSING = "READY_FOR_PROCESSING"
FAILED_DATA_STORAGE = "FAILED_DATA_STORAGE"
FAILED_FILE_STORAGE = "FAILED_FILE_STORAGE"

INGESTION_FAILURE_STATUSES = frozenset({FAILED_DATA_STORAGE, FAILED_FILE_STORAGE})
INGESTION_TERMINAL_STATUSES = frozenset({READY_FOR_PROCESSING, *INGESTION_FAILURE_STATUSES})

# Workflow-engine phase — not implemented yet.
QUEUED = "QUEUED"
RUNNING = "RUNNING"
PROCESSED = "PROCESSED"
PROCESSED_BUT_NEED_TO_VALIDATE = "PROCESSED_BUT_NEED_TO_VALIDATE"
FAILED = "FAILED"
