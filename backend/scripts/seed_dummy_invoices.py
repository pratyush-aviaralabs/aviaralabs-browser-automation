"""One-off dev seed: a handful of dummy Invoice rows so the web UI's
Invoices table has something to paginate through before real ingestion
exists. Safe to re-run — it's a no-op once any invoice rows already exist."""

from datetime import date, timedelta

from dealercenter_agent.config import DBConfig
from dealercenter_agent.db.engine import get_session_factory
from dealercenter_agent.db.models import Invoice, Tenant

DUMMY_INVOICES = [
    dict(invoice_number="INV-10234", vendor_name="Midwest Auto Parts", grand_total=119.12,
         vin="1FTFW1E81NFA67507", stock_number=None, category="Repair"),
    dict(invoice_number="INV-10235", vendor_name="Precision Body Shop", grand_total=248.50,
         vin="1FTFW1E81NFG16336", stock_number=None, category="Repair"),
    dict(invoice_number="INV-10236", vendor_name="AutoGlass Direct", grand_total=85.00,
         vin=None, stock_number="22421", category="Other"),
    dict(invoice_number="INV-10237", vendor_name="Metro Detailing Co", grand_total=60.00,
         vin=None, stock_number="30187", category="Other"),
    dict(invoice_number="INV-10238", vendor_name="Reliable Tire & Wheel", grand_total=312.75,
         vin=None, stock_number="30411", category="Repair"),
    dict(invoice_number="INV-10239", vendor_name="Sunrise Paint & Collision", grand_total=540.00,
         vin=None, stock_number="30512", category="Repair"),
    dict(invoice_number="INV-10240", vendor_name="Capital Windshield Repair", grand_total=145.25,
         vin=None, stock_number="30599", category="Other"),
    dict(invoice_number="INV-10241", vendor_name="Northside Transmission", grand_total=975.00,
         vin=None, stock_number="30620", category="Repair"),
    dict(invoice_number="INV-10242", vendor_name="Quick Lube Express", grand_total=42.99,
         vin=None, stock_number="30677", category="Other"),
    dict(invoice_number="INV-10243", vendor_name="Elite Upholstery", grand_total=210.00,
         vin=None, stock_number="30701", category="Other"),
    dict(invoice_number="INV-10244", vendor_name="Vanguard Brake & Suspension", grand_total=389.40,
         vin=None, stock_number="30744", category="Repair"),
    dict(invoice_number="INV-10245", vendor_name="Horizon Battery Supply", grand_total=129.99,
         vin=None, stock_number="30810", category="Other"),
]


def main() -> None:
    session_factory = get_session_factory(DBConfig())
    with session_factory() as session:
        if session.query(Invoice).count() > 0:
            print("invoices table already has rows — skipping seed")
            return

        tenant = session.query(Tenant).first()
        if tenant is None:
            tenant = Tenant(name="Dev Dealer")
            session.add(tenant)
            session.flush()

        today = date.today()
        for i, data in enumerate(DUMMY_INVOICES):
            category = data.pop("category")
            session.add(
                Invoice(
                    tenant_id=tenant.id,
                    invoice_date=today - timedelta(days=i),
                    status="QUEUED",
                    callback_url="",
                    payload_json={"category": category},
                    **data,
                )
            )
        session.commit()
        print(f"seeded {len(DUMMY_INVOICES)} dummy invoices for tenant {tenant.id}")


if __name__ == "__main__":
    main()
