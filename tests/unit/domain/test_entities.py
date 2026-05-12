"""RED: domain entity invariants — run before any implementation exists."""
import pytest
from cookie_refresher.domain.entities import SessionCookies, AgentResult, Job, JobStatus


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


class TestJob:
    def test_new_job_has_processing_status(self):
        job = Job(id="abc-123", status=JobStatus.PROCESSING)
        assert job.status == JobStatus.PROCESSING
        assert job.id == "abc-123"
        assert job.steps_taken is None
        assert job.error is None

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
