"""RED: domain entity invariants — run before any implementation exists."""
import pytest
from datetime import datetime
from cookie_refresher.domain.entities import SessionCookies, AgentResult, Job, JobStatus, RecordedStep, ActionScript


class TestSessionCookies:
    def test_valid_creation(self):
        cookies = SessionCookies(cf_clearance="abc123", ci_session="xyz789")
        assert cookies.cf_clearance == "abc123"
        assert cookies.ci_session == "xyz789"

    def test_empty_cf_clearance_raises(self):
        with pytest.raises(ValueError, match="cf_clearance"):
            SessionCookies(cf_clearance="", ci_session="xyz")

    def test_empty_ci_session_raises(self):
        with pytest.raises(ValueError, match="ci_session"):
            SessionCookies(cf_clearance="abc", ci_session="")

    def test_whitespace_only_values_raise(self):
        with pytest.raises(ValueError):
            SessionCookies(cf_clearance="   ", ci_session="xyz")

    def test_is_immutable(self):
        cookies = SessionCookies(cf_clearance="abc", ci_session="xyz")
        with pytest.raises((AttributeError, TypeError)):
            cookies.cf_clearance = "tampered"  # type: ignore[misc]

    def test_equality_by_value(self):
        a = SessionCookies(cf_clearance="abc", ci_session="xyz")
        b = SessionCookies(cf_clearance="abc", ci_session="xyz")
        assert a == b

    def test_inequality_when_values_differ(self):
        a = SessionCookies(cf_clearance="abc", ci_session="xyz")
        b = SessionCookies(cf_clearance="abc", ci_session="other")
        assert a != b


class TestAgentResult:
    def test_success_factory_sets_correct_fields(self):
        cookies = SessionCookies(cf_clearance="abc", ci_session="xyz")
        result = AgentResult.ok(cookies, steps_taken=5)

        assert result.success is True
        assert result.cookies == cookies
        assert result.error is None
        assert result.steps_taken == 5

    def test_failure_factory_sets_correct_fields(self):
        result = AgentResult.fail("Login page not found", steps_taken=3)

        assert result.success is False
        assert result.cookies is None
        assert "Login page" in result.error
        assert result.steps_taken == 3

    def test_success_requires_cookies(self):
        with pytest.raises((TypeError, ValueError)):
            AgentResult.ok(None, steps_taken=1)  # type: ignore[arg-type]

    def test_failure_requires_error_message(self):
        with pytest.raises((TypeError, ValueError)):
            AgentResult.fail("", steps_taken=1)

    def test_ok_carries_messages(self):
        cookies = SessionCookies(cf_clearance="abc", ci_session="xyz")
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
        result = AgentResult.ok(cookies, steps_taken=1, messages=msgs)
        assert result.messages == msgs

    def test_fail_carries_messages(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
        result = AgentResult.fail("boom", steps_taken=1, messages=msgs)
        assert result.messages == msgs

    def test_messages_defaults_to_empty_list(self):
        cookies = SessionCookies(cf_clearance="abc", ci_session="xyz")
        result = AgentResult.ok(cookies, steps_taken=1)
        assert result.messages == []


class TestJob:
    def test_new_job_has_processing_status(self):
        job = Job(id="abc-123", status=JobStatus.PROCESSING)
        assert job.status == JobStatus.PROCESSING
        assert job.id == "abc-123"
        assert job.steps_taken is None
        assert job.error is None
        assert job.messages == []

    def test_job_status_values(self):
        assert JobStatus.PROCESSING == "processing"
        assert JobStatus.SUCCESS == "success"
        assert JobStatus.FAILED == "failed"

    def test_job_is_mutable(self):
        job = Job(id="x", status=JobStatus.PROCESSING)
        job.status = JobStatus.SUCCESS
        job.steps_taken = 7
        assert job.status == JobStatus.SUCCESS
        assert job.steps_taken == 7


class TestRecordedStep:
    def test_valid_creation(self):
        step = RecordedStep(
            action_type="left_click",
            params={"coordinate": [642, 470]},
            delay_after_ms=520.0,
        )
        assert step.action_type == "left_click"
        assert step.params == {"coordinate": [642, 470]}
        assert step.delay_after_ms == 520.0

    def test_is_immutable(self):
        step = RecordedStep(action_type="key", params={"text": "F12"}, delay_after_ms=100.0)
        with pytest.raises((AttributeError, TypeError)):
            step.action_type = "left_click"  # type: ignore[misc]

    def test_equality_by_value(self):
        a = RecordedStep(action_type="type", params={"text": "{{email}}"}, delay_after_ms=200.0)
        b = RecordedStep(action_type="type", params={"text": "{{email}}"}, delay_after_ms=200.0)
        assert a == b

    def test_inequality_when_params_differ(self):
        a = RecordedStep(action_type="left_click", params={"coordinate": [100, 200]}, delay_after_ms=100.0)
        b = RecordedStep(action_type="left_click", params={"coordinate": [300, 400]}, delay_after_ms=100.0)
        assert a != b

    def test_masked_credential_sentinel_stored(self):
        step = RecordedStep(action_type="type", params={"text": "{{password}}"}, delay_after_ms=300.0)
        assert step.params["text"] == "{{password}}"

    def test_scroll_params_stored(self):
        step = RecordedStep(
            action_type="scroll",
            params={"coordinate": [740, 800], "scroll_direction": "down", "scroll_amount": 5},
            delay_after_ms=150.0,
        )
        assert step.params["scroll_direction"] == "down"
        assert step.params["scroll_amount"] == 5


class TestActionScript:
    def test_valid_creation(self):
        steps = [RecordedStep("left_click", {"coordinate": [100, 200]}, 500.0)]
        recorded_at = datetime(2026, 5, 13, 12, 0, 0)
        script = ActionScript(steps=steps, recorded_at=recorded_at)
        assert script.steps == steps
        assert script.recorded_at == recorded_at
        assert script.use_count == 0

    def test_use_count_defaults_to_zero(self):
        script = ActionScript(steps=[], recorded_at=datetime.utcnow())
        assert script.use_count == 0

    def test_is_mutable(self):
        script = ActionScript(steps=[], recorded_at=datetime.utcnow())
        script.use_count = 5
        assert script.use_count == 5

    def test_steps_list_preserves_order(self):
        s1 = RecordedStep("left_click", {"coordinate": [100, 200]}, 500.0)
        s2 = RecordedStep("type", {"text": "{{email}}"}, 300.0)
        s3 = RecordedStep("key", {"text": "Return"}, 100.0)
        script = ActionScript(steps=[s1, s2, s3], recorded_at=datetime.utcnow())
        assert script.steps[0] == s1
        assert script.steps[1] == s2
        assert script.steps[2] == s3
