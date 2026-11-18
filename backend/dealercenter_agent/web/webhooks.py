"""Invoice ingestion webhook + its status-check callback. The webhook
handler always creates a fresh Invoice row and enqueues the slow part —
downloading the PDF — onto Redis/RQ (see ingestion/jobs.py), so the
sender's webhook ack is never held up by a blob-storage download.

Confirmed required (2026-11-18): duplicate invoice_id deliveries are
deliberately NOT deduped at this layer — every delivery, including a
resend of the same invoice_id, creates its own Invoice row and runs
through the full pipeline. A true duplicate still gets caught
downstream, at the point that actually matters: CostAddTool's own
memo-matching check against DealerCenter's live data, which reports
EXISTS instead of adding a second entry (see ingestion/callback.py's
created/exists mapping).

Both handlers are plain `def`, not `async def` — confirmed required
(2026-11-18, found via a 500-request burst load test): `async def` with
synchronous SQLAlchemy calls inside blocks the single event-loop thread
for every concurrent request, which under a webhook burst caused
requests to pile up and time out client-side. A sync `def` endpoint has
FastAPI run it in its worker threadpool instead, so concurrent webhook
deliveries actually run concurrently rather than serializing on one
thread."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..config import DBConfig
from ..db.engine import get_session_factory
from ..db.models import Invoice, Tenant
from ..ingestion import status as s
from ..ingestion.jobs import process_invoice_ingestion
from ..ingestion.mapper import InvalidWebhookPayload, invoice_fields_from_payload
from ..queue.connection import get_invoice_ingestion_queue

router = APIRouter()

_SESSION_FACTORY = get_session_factory(DBConfig())


def _get_or_create_default_tenant(session) -> Tenant:
    tenant = session.scalar(select(Tenant).limit(1))
    if tenant is None:
        tenant = Tenant(name="Default Tenant")
        session.add(tenant)
        session.flush()
    return tenant


@router.post("/webhooks/invoices")
def ingest_invoice_webhook(payload: dict):
    try:
        fields = invoice_fields_from_payload(payload)
    except InvalidWebhookPayload as exc:
        raise HTTPException(422, str(exc))

    with _SESSION_FACTORY() as session:
        tenant = _get_or_create_default_tenant(session)
        invoice = Invoice(
            tenant_id=tenant.id,
            invoice_number=fields["invoice_number"],
            vendor_name=fields["vendor_name"],
            grand_total=fields["grand_total"],
            invoice_date=fields["invoice_date"],
            vin=fields["vin"],
            po_number=fields["po_number"],
            stock_number=fields["stock_number"],
            status=s.RECEIVED,
            external_invoice_id=fields["external_invoice_id"],
            callback_url=fields["callback_url"],
            callback_token=fields["callback_token"],
            payload_json={
                "category": fields["category"],
                "file_url": fields["file_url"],
                "attachments": fields["attachments"],
                "raw_data": fields["raw_data"],
            },
        )
        session.add(invoice)
        session.commit()
        invoice_id = invoice.id

        try:
            get_invoice_ingestion_queue().enqueue(process_invoice_ingestion, invoice_id)
        except Exception as exc:
            # Confirmed live (2026-11-18): if enqueue fails (Redis down)
            # after the row's already committed, leaving it behind as a
            # dead row stuck at RECEIVED forever (nothing will ever pick
            # it up) is worse than losing it — roll the insert back so
            # the sender's retry creates a fresh row that actually gets
            # queued, instead of accumulating permanently-stuck rows.
            session.delete(invoice)
            session.commit()
            raise HTTPException(503, f"failed to enqueue ingestion job: {exc}")

    return {"status": "accepted", "invoice_id": invoice_id}


@router.get("/organization/connectors/dealercenter/callback/{token}/")
def invoice_ingestion_status(token: str):
    with _SESSION_FACTORY() as session:
        # Duplicates are now allowed (see module docstring), so more
        # than one row can share the same callback_token — e.g. an
        # exact retry delivered with the same callback_url. Always
        # resolve to the most recently created one.
        invoice = session.scalar(
            select(Invoice).where(Invoice.callback_token == token).order_by(Invoice.id.desc())
        )
    if invoice is None:
        raise HTTPException(404, "unknown callback token")
    return {
        "invoice_id": invoice.external_invoice_id,
        "invoice_number": invoice.invoice_number,
        "status": invoice.status,
        "local_pdf_path": invoice.local_pdf_path,
    }
