"""Tests for the FastAPI controller layer."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from cookie_refresher.adapters.controllers import api as api_module
from cookie_refresher.adapters.controllers.api import router
from cookie_refresher.adapters.job_store import InMemoryJobStore
from cookie_refresher.domain.entities import AgentResult, SessionCookies

COOKIES = SessionCookies(cf_clearance="cf_tok", ci_session="ci_tok")


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


class TestHealthEndpoint:
    def test_returns_200_with_ok_status(self):
        client = TestClient(_make_test_app())
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["service"] == "cookie-refresher"


class TestPostRefreshEndpoint:
    def setup_method(self):
        self.store = InMemoryJobStore()
        api_module.set_job_store(self.store)

    def teardown_method(self):
        api_module._use_case_factory = None
        api_module._job_store = None

    def test_returns_503_when_not_initialised(self):
        api_module._use_case_factory = None
        client = TestClient(_make_test_app())
        response = client.post("/refresh")
        assert response.status_code == 503

    def test_returns_202_with_processing_status(self):
        mock_use_case = AsyncMock()
        mock_use_case.execute.return_value = AgentResult.ok(COOKIES, steps_taken=4)
        api_module.set_use_case_factory(lambda: mock_use_case)

        client = TestClient(_make_test_app())
        response = client.post("/refresh")

        assert response.status_code == 202
        body = response.json()
        assert "job_id" in body
        assert body["job_id"]

    def test_background_task_updates_store_on_success(self):
        mock_use_case = AsyncMock()
        mock_use_case.execute.return_value = AgentResult.ok(COOKIES, steps_taken=4)
        api_module.set_use_case_factory(lambda: mock_use_case)

        client = TestClient(_make_test_app())
        response = client.post("/refresh")
        job_id = response.json()["job_id"]

        status_response = client.get(f"/refresh/{job_id}")
        assert status_response.status_code == 200
        body = status_response.json()
        assert body["status"] == "success"
        assert body["steps_taken"] == 4
        assert body["error"] is None

    def test_status_response_includes_messages(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "task"}]}]
        mock_use_case = AsyncMock()
        mock_use_case.execute.return_value = AgentResult.ok(COOKIES, steps_taken=1, messages=msgs)
        api_module.set_use_case_factory(lambda: mock_use_case)

        client = TestClient(_make_test_app())
        job_id = client.post("/refresh").json()["job_id"]

        body = client.get(f"/refresh/{job_id}").json()
        assert body["messages"] == msgs

    def test_background_task_updates_store_on_failure(self):
        mock_use_case = AsyncMock()
        mock_use_case.execute.return_value = AgentResult.fail(
            "Max steps exceeded", steps_taken=100
        )
        api_module.set_use_case_factory(lambda: mock_use_case)

        client = TestClient(_make_test_app())
        response = client.post("/refresh")
        job_id = response.json()["job_id"]

        status_response = client.get(f"/refresh/{job_id}")
        body = status_response.json()
        assert body["status"] == "failed"
        assert body["error"] == "Max steps exceeded"


class TestGetRefreshStatusEndpoint:
    def setup_method(self):
        self.store = InMemoryJobStore()
        api_module.set_job_store(self.store)

    def teardown_method(self):
        api_module._use_case_factory = None
        api_module._job_store = None

    def test_returns_404_for_unknown_job(self):
        client = TestClient(_make_test_app())
        response = client.get("/refresh/nonexistent-id")
        assert response.status_code == 404

    def test_returns_503_when_store_not_initialised(self):
        api_module._job_store = None
        client = TestClient(_make_test_app())
        response = client.get("/refresh/any-id")
        assert response.status_code == 503
