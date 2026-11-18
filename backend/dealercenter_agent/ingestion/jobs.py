"""RQ job: the slow half of ingesting a webhook'd invoice. The webhook
handler itself only does a fast DB upsert and enqueues this — materializing
the PDF is the part that can be slow or transiently fail, and at 500-600
invoices/day it has to happen off the request thread so that never holds
up the sender's webhook ack.

Source priority, confirmed required (2026-11-18):
1. An attachment that carries the file's actual bytes inline
   (content_base64) — no network call needed, so it's checked first and
   used immediately if present.
2. Otherwise, URL-based download: file_url first, then each attachment's
   own url, in order, as a fallback — the sender includes both, and
   file_url going stale by the time we get to it shouldn't fail the
   whole ingestion if an attachment copy is still good."""

import base64
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx

from ..config import DBConfig, StorageConfig
from ..db.engine import get_session_factory
from ..db.models import Invoice
from . import status as s
from .callback import notify_dealercenter_result


class SasTokenExpiredError(Exception):
    """Raised instead of letting a plain 401/403 (or a proactive expiry
    check) read as a generic, unexplained download failure."""


def _sas_expiry(url: str) -> datetime | None:
    """Azure Blob Storage SAS URLs carry their expiry in the `se` query
    param (e.g. ...&se=2026-11-18T04%3A41%3A02Z&...). Returns it as a
    UTC datetime, or None if this isn't a SAS-signed URL (or the `se`
    value doesn't parse)."""
    se_values = parse_qs(urlparse(url).query).get("se")
    if not se_values:
        return None
    try:
        return datetime.fromisoformat(se_values[0].replace("Z", "+00:00"))
    except ValueError:
        return None


def _check_sas_not_expired(url: str) -> None:
    """Confirmed live (2026-11-18): these signed URLs are valid ~1 hour
    from issuance, and draining a 500-invoice backlog can itself take
    close to that long (see the load-test numbers) — by the time a job
    gets to downloading, the token may already be dead. Checked
    proactively, before even making the request, so that failure mode
    is reported as exactly what it is — not a generic network error —
    and doesn't waste a request on a link that's certain to fail."""
    expiry = _sas_expiry(url)
    if expiry is None:
        return
    now = datetime.now(timezone.utc)
    if now >= expiry:
        raise SasTokenExpiredError(
            f"SAS token expired at {expiry.isoformat()} (now {now.isoformat()}) — signed URLs here "
            "are valid ~1 hour from issuance; if ingestion is queued longer than that before this "
            "file gets downloaded, the link needs to be re-issued, or the backlog reduced below ~1 hour."
        )

# Confirmed live (2026-11-18), found via a 500-request burst load test:
# building a fresh session_factory() per job call means a fresh engine
# and a fresh connection pool — a full TCP+TLS handshake to Supabase
# (ap-northeast-1) on every single job, ~3.8-4.2s measured, dwarfing
# everything else the job actually does. Built once per worker process
# instead, and reused across every job that worker processes, so only
# the very first job pays the cold-connect cost.
_SESSION_FACTORY = get_session_factory(DBConfig())


def _download(url: str, dest_path) -> None:
    _check_sas_not_expired(url)
    try:
        with httpx.stream("GET", url, timeout=30.0, follow_redirects=True) as response:
            response.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)
    except httpx.HTTPStatusError as exc:
        # The proactive check above catches the common case, but clock
        # skew or a token invalidated early means Azure can still
        # reject it with 401/403 even when our own check passed —
        # reclassify that the same way rather than letting it read as
        # an unexplained auth failure.
        if exc.response.status_code in (401, 403) and _sas_expiry(url) is not None:
            raise SasTokenExpiredError(
                f"SAS token rejected by the server (HTTP {exc.response.status_code}) — expired or "
                f"invalidated despite passing the proactive expiry check (possible clock skew): {exc}"
            ) from exc
        raise


def _save_inline(content_base64: str, dest_path) -> None:
    with open(dest_path, "wb") as f:
        f.write(base64.b64decode(content_base64))


def _materialize_file(file_url: str | None, attachments: list[dict], dest_path) -> tuple[str, str]:
    """Attachment-embedded media wins immediately if present; otherwise
    falls back to URL-based download (file_url, then each attachment's
    url, in order). Returns (source_label, source) of whichever
    candidate succeeded. Raises the last error if every candidate failed."""
    for i, attachment in enumerate(attachments):
        content_base64 = attachment.get("content_base64")
        if not content_base64:
            continue
        try:
            _save_inline(content_base64, dest_path)
            return f"attachment[{i}]_inline", attachment.get("filename") or f"attachment-{i}"
        except Exception:  # noqa: BLE001 - malformed inline content, fall through to URL attempts
            pass

    candidates = ([("file_url", file_url)] if file_url else []) + [
        (f"attachment[{i}]", a["url"]) for i, a in enumerate(attachments) if a.get("url")
    ]
    if not candidates:
        raise ValueError("no attachment content/url or file_url to materialize the file from")

    last_error: Exception | None = None
    for source_label, url in candidates:
        try:
            _download(url, dest_path)
            return source_label, url
        except Exception as exc:  # noqa: BLE001 - deliberately broad, try the next candidate
            last_error = exc
    raise last_error


def process_invoice_ingestion(invoice_id: int) -> None:
    """Ingestion has exactly two ways to fail, and the status names which
    one: FAILED_DATA_STORAGE if persisting the invoice itself fails (the
    INGESTING transition below is the one DB write this job does before
    touching the file — if THAT fails, it's a data-storage failure, not
    a file one); FAILED_FILE_STORAGE if file_url and every attachment
    fallback all fail to download."""
    storage = StorageConfig()

    with _SESSION_FACTORY() as session:
        invoice = session.get(Invoice, invoice_id)
        if invoice is None:
            return

        try:
            invoice.status = s.INGESTING
            session.commit()
        except Exception:
            session.rollback()
            try:
                invoice.status = s.FAILED_DATA_STORAGE
                session.commit()
                notify_dealercenter_result(
                    invoice,
                    status="failed",
                    message="internal error while saving the invoice record — please resend",
                )
            except Exception:
                pass  # DB itself unreachable — nothing more to do here
            return

        payload_json = invoice.payload_json or {}
        file_url = payload_json.get("file_url")
        attachments = payload_json.get("attachments") or []

        try:
            if file_url or attachments:
                storage.storage_local_path.mkdir(parents=True, exist_ok=True)
                dest_path = storage.storage_local_path / f"invoice-{invoice.external_invoice_id}.pdf"
                source_label, source = _materialize_file(file_url, attachments, dest_path)
                invoice.local_pdf_path = str(dest_path)
                payload_json = {**payload_json, "downloaded_from": source_label, "downloaded_source": source}
            invoice.status = s.READY_FOR_PROCESSING
            invoice.payload_json = payload_json
        except Exception as exc:
            invoice.status = s.FAILED_FILE_STORAGE
            invoice.payload_json = {**payload_json, "ingestion_error": str(exc)}

        session.commit()
        if invoice.status == s.FAILED_FILE_STORAGE:
            notify_dealercenter_result(
                invoice,
                status="failed",
                message=(invoice.payload_json or {}).get("ingestion_error") or "could not download the invoice file",
            )
