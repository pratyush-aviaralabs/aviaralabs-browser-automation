"""Maps a raw ingestion webhook payload onto the Invoice table's columns.
Everything the model doesn't have a dedicated column for (due_date,
ro_number, vendor contact details, bill-to, line_items, subtotal,
total_tax, ...) is kept verbatim in payload_json — nothing from the
payload is discarded, it's just not all first-class columns."""

from datetime import date

from .classify import classify_category


class InvalidWebhookPayload(ValueError):
    pass


def _require(d: dict, key: str, where: str) -> object:
    value = d.get(key)
    if value in (None, ""):
        raise InvalidWebhookPayload(f"missing required field '{where}.{key}'")
    return value


def _extract_callback_token(callback_url: str) -> str | None:
    segments = [s for s in callback_url.rstrip("/").split("/") if s]
    return segments[-1] if segments else None


def _normalize_attachments(payload: dict) -> list[dict]:
    """Normalizes attachments to a list of dicts, each either
    {"content_base64": ..., "filename": ...} (the attachment carries the
    actual file media inline) or {"url": ...} (a reference to download
    from) — accepts plain URL strings too, for senders that don't wrap
    them in an object. Confirmed required (2026-11-18): an attachment
    can hold the real file directly, not just a link to it — ingestion
    checks for that first (see ingestion/jobs.py), before falling back
    to any URL-based download."""
    attachments = payload.get("attachments") or []
    normalized = []
    for attachment in attachments:
        if isinstance(attachment, str):
            normalized.append({"url": attachment})
        elif isinstance(attachment, dict):
            if attachment.get("content_base64"):
                normalized.append(
                    {"content_base64": attachment["content_base64"], "filename": attachment.get("filename")}
                )
            elif attachment.get("url"):
                normalized.append({"url": attachment["url"]})
    return normalized


def invoice_fields_from_payload(payload: dict) -> dict:
    """Returns a flat dict ready to spread into an Invoice(...) constructor,
    plus a couple of extra keys (category, file_url, raw_data) the caller
    folds into payload_json itself."""
    data = payload.get("data") or {}

    external_invoice_id = _require(payload, "invoice_id", "payload")
    callback_url = _require(payload, "callback_url", "payload")
    invoice_number = _require(data, "invoice_number", "data")
    vendor_name = _require(data, "vendor_name", "data")
    grand_total = _require(data, "grand_total", "data")
    invoice_date_raw = _require(data, "invoice_date", "data")

    try:
        invoice_date = date.fromisoformat(invoice_date_raw)
    except ValueError as exc:
        raise InvalidWebhookPayload(f"invalid data.invoice_date '{invoice_date_raw}'") from exc

    return {
        "external_invoice_id": str(external_invoice_id),
        "callback_url": callback_url,
        "callback_token": _extract_callback_token(callback_url),
        "invoice_number": invoice_number,
        "vendor_name": vendor_name,
        "grand_total": grand_total,
        "invoice_date": invoice_date,
        "vin": data.get("vin") or None,
        "po_number": data.get("po_number") or None,
        "stock_number": data.get("stock_number") or None,
        "category": classify_category(data.get("line_items")),
        "file_url": payload.get("file_url"),
        "attachments": _normalize_attachments(payload),
        "raw_data": data,
    }
