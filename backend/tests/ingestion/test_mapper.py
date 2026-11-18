from datetime import date

import pytest

from dealercenter_agent.ingestion.mapper import InvalidWebhookPayload, invoice_fields_from_payload

SAMPLE_PAYLOAD = {
    "data": {
        "document_type": "invoice",
        "invoice_number": "INV-1042",
        "invoice_date": "2026-11-03",
        "due_date": "2026-12-03",
        "po_number": None,
        "ro_number": "RO-88123",
        "vendor_name": "Hillside Auto Detail LLC",
        "vendor_email": "billing@hillsideauto.example",
        "vendor_phone": "(555) 013-2290",
        "vendor_address": "123 Hillside Ave, Columbus, OH 43215",
        "vendor_account_number": "TG-20417",
        "bill_to_name": "Tailgate Motors",
        "bill_to_address": "456 Main St, Dayton, OH 45402",
        "stock_number": "ST-77812",
        "vin": "1HGCM82633A004352",
        "vehicle_description": "2003 Honda Accord EX-L",
        "odometer": "48210",
        "line_items": [
            {"description": "Bumper repair - body detail", "quantity": 1, "unit_price": 220.00, "total": 220.00},
            {"description": "Shop supplies", "quantity": 1, "unit_price": 85.00, "total": 85.00},
        ],
        "subtotal": 305,
        "total_tax": 22,
        "grand_total": 327.88,
    },
    "file_url": "https://blob.example.com/vendor-invoices/INV-1042.pdf?sas=fresh-token",
    "attachments": [
        {"url": "https://blob.example.com/attachments/INV-1042-copy.pdf?sas=fresh-token-2", "filename": "INV-1042-copy.pdf"},
    ],
    "invoice_id": "665f1c2e9a3b4d0012ab34cd",
    "callback_url": "https://backend.example.com/organization/connectors/dealercenter/callback/abc123signed/",
}


def test_maps_core_fields_onto_invoice_columns():
    fields = invoice_fields_from_payload(SAMPLE_PAYLOAD)
    assert fields["external_invoice_id"] == "665f1c2e9a3b4d0012ab34cd"
    assert fields["callback_url"] == SAMPLE_PAYLOAD["callback_url"]
    assert fields["callback_token"] == "abc123signed"
    assert fields["invoice_number"] == "INV-1042"
    assert fields["vendor_name"] == "Hillside Auto Detail LLC"
    assert fields["grand_total"] == 327.88
    assert fields["invoice_date"] == date(2026, 11, 3)
    assert fields["vin"] == "1HGCM82633A004352"
    assert fields["stock_number"] == "ST-77812"
    assert fields["po_number"] is None


def test_classifies_category_from_line_items():
    fields = invoice_fields_from_payload(SAMPLE_PAYLOAD)
    assert fields["category"] == "Repair"


def test_keeps_file_url_and_full_raw_data_for_payload_json():
    fields = invoice_fields_from_payload(SAMPLE_PAYLOAD)
    assert fields["file_url"] == SAMPLE_PAYLOAD["file_url"]
    assert fields["raw_data"]["ro_number"] == "RO-88123"


def test_normalizes_attachment_url_dict_form():
    fields = invoice_fields_from_payload(SAMPLE_PAYLOAD)
    assert fields["attachments"] == [
        {"url": "https://blob.example.com/attachments/INV-1042-copy.pdf?sas=fresh-token-2"}
    ]


def test_normalizes_attachment_plain_string_form():
    payload = {**SAMPLE_PAYLOAD, "attachments": ["https://blob.example.com/plain-string-attachment.pdf"]}
    fields = invoice_fields_from_payload(payload)
    assert fields["attachments"] == [{"url": "https://blob.example.com/plain-string-attachment.pdf"}]


def test_normalizes_attachment_inline_content_form():
    payload = {
        **SAMPLE_PAYLOAD,
        "attachments": [{"content_base64": "ZmFrZS1wZGYtYnl0ZXM=", "filename": "invoice.pdf"}],
    }
    fields = invoice_fields_from_payload(payload)
    assert fields["attachments"] == [{"content_base64": "ZmFrZS1wZGYtYnl0ZXM=", "filename": "invoice.pdf"}]


def test_attachments_defaults_to_empty_list_when_absent():
    payload = {k: v for k, v in SAMPLE_PAYLOAD.items() if k != "attachments"}
    fields = invoice_fields_from_payload(payload)
    assert fields["attachments"] == []


def test_raises_on_missing_required_field():
    bad_payload = {**SAMPLE_PAYLOAD, "invoice_id": None}
    with pytest.raises(InvalidWebhookPayload):
        invoice_fields_from_payload(bad_payload)


def test_raises_on_missing_data_field():
    bad_data = {k: v for k, v in SAMPLE_PAYLOAD["data"].items() if k != "grand_total"}
    bad_payload = {**SAMPLE_PAYLOAD, "data": bad_data}
    with pytest.raises(InvalidWebhookPayload):
        invoice_fields_from_payload(bad_payload)


def test_raises_on_unparseable_invoice_date():
    bad_data = {**SAMPLE_PAYLOAD["data"], "invoice_date": "not-a-date"}
    bad_payload = {**SAMPLE_PAYLOAD, "data": bad_data}
    with pytest.raises(InvalidWebhookPayload):
        invoice_fields_from_payload(bad_payload)
