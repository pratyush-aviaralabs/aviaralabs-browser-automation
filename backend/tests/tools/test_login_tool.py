import pytest

from dealercenter_agent.tools.base import StepFailure, ToolStatus
from dealercenter_agent.tools.login_tool import MFA_CODE_SELECTOR, LoginTool


def test_login_reuses_valid_saved_session(fake_driver_already_logged_in):
    result = LoginTool().run(
        fake_driver_already_logged_in,
        username="u",
        password="p",
        mfa_code_provider=lambda: "000000",
    )
    assert result.status == ToolStatus.SUCCESS
    assert result.data["reused_session"] is True
    assert fake_driver_already_logged_in.submit_clicks == []


def test_login_detects_mfa_and_uses_provided_code(fake_driver_requires_mfa):
    result = LoginTool().run(
        fake_driver_requires_mfa,
        username="u",
        password="p",
        mfa_code_provider=lambda: "123456",
    )
    assert result.status == ToolStatus.SUCCESS
    assert result.data["reused_session"] is False
    assert fake_driver_requires_mfa.filled[MFA_CODE_SELECTOR] == "123456"


def test_login_raises_step_failure_when_mfa_code_rejected(fake_driver_always_rejects_mfa):
    with pytest.raises(StepFailure) as exc:
        LoginTool().run(
            fake_driver_always_rejects_mfa,
            username="u",
            password="p",
            mfa_code_provider=lambda: "000000",
        )
    assert exc.value.tool_name == "LoginTool"
    assert "rejected" in exc.value.reason


def test_login_raises_step_failure_on_malformed_mfa_code(fake_driver_requires_mfa):
    with pytest.raises(StepFailure) as exc:
        LoginTool().run(
            fake_driver_requires_mfa,
            username="u",
            password="p",
            mfa_code_provider=lambda: "abc",
        )
    assert "invalid MFA code format" in exc.value.reason
