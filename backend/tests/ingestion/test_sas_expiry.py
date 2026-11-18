from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dealercenter_agent.ingestion import jobs

LIVE_SAS_URL_SHAPE = (
    "https://aviaraconnectstorage.blob.core.windows.net/aviara-connect/vendor_invoices/"
    "Tailgate%20Motor%20Co/Invoice-110011796__c209f4d18638.pdf"
    "?se={expiry}&sp=r&sv=2025-07-05&sr=b&sig=KmjPmAyPplCjBaMoPkGSiEisc9iUuyCaGuEIwv%2B2B2Q%3D"
)


def _url_with_expiry(delta: timedelta) -> str:
    expiry = (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H%%3A%M%%3A%SZ")
    return LIVE_SAS_URL_SHAPE.format(expiry=expiry)


def test_sas_expiry_parses_the_se_query_param():
    url = _url_with_expiry(timedelta(hours=1))
    expiry = jobs._sas_expiry(url)
    assert expiry is not None
    assert abs((expiry - (datetime.now(timezone.utc) + timedelta(hours=1))).total_seconds()) < 5


def test_sas_expiry_returns_none_for_a_non_sas_url():
    assert jobs._sas_expiry("https://example.com/plain-file.pdf") is None


def test_sas_expiry_returns_none_for_the_docusensa_non_expiring_url_shape():
    # Confirmed live (2026-11-18): a second real sender, DocuSensa,
    # hands back plain unsigned blob URLs with no query string at all —
    # the same file_url field can carry either shape, and a URL with no
    # `se` param must never be treated as expired.
    url = "https://ducusensastorage.blob.core.windows.net/docusensa/media/b147f5a8-b76b-45a6-9494-61f9cb45eb7a.pdf"
    assert jobs._sas_expiry(url) is None
    jobs._check_sas_not_expired(url)  # must not raise


def test_check_sas_not_expired_passes_for_a_future_expiry():
    url = _url_with_expiry(timedelta(minutes=30))
    jobs._check_sas_not_expired(url)  # must not raise


def test_check_sas_not_expired_raises_clearly_for_an_already_expired_token():
    url = _url_with_expiry(-timedelta(minutes=5))
    with pytest.raises(jobs.SasTokenExpiredError, match="SAS token expired"):
        jobs._check_sas_not_expired(url)


def test_download_raises_sas_expired_without_making_a_request_when_already_expired(monkeypatch, tmp_path):
    url = _url_with_expiry(-timedelta(minutes=5))

    def fail_if_called(*args, **kwargs):
        raise AssertionError("httpx.stream must not be called — the proactive check should catch this first")

    monkeypatch.setattr(httpx, "stream", fail_if_called)

    with pytest.raises(jobs.SasTokenExpiredError):
        jobs._download(url, tmp_path / "out.pdf")


def test_download_reclassifies_a_403_on_a_sas_url_as_expired(monkeypatch, tmp_path):
    url = _url_with_expiry(timedelta(minutes=30))  # passes the proactive check...

    class _FakeResponse:
        status_code = 403

        def raise_for_status(self):
            raise httpx.HTTPStatusError("403 Forbidden", request=None, response=self)

    class _FakeStreamContext:
        def __enter__(self):
            return _FakeResponse()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(httpx, "stream", lambda *a, **kw: _FakeStreamContext())

    with pytest.raises(jobs.SasTokenExpiredError, match="rejected by the server"):
        jobs._download(url, tmp_path / "out.pdf")


def test_download_does_not_reclassify_a_403_on_a_non_sas_url(monkeypatch, tmp_path):
    class _FakeResponse:
        status_code = 403

        def raise_for_status(self):
            raise httpx.HTTPStatusError("403 Forbidden", request=None, response=self)

    class _FakeStreamContext:
        def __enter__(self):
            return _FakeResponse()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(httpx, "stream", lambda *a, **kw: _FakeStreamContext())

    with pytest.raises(httpx.HTTPStatusError):
        jobs._download("https://example.com/plain-file.pdf", tmp_path / "out.pdf")
