"""Tests for the FastAPI controller layer."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from cookie_refresher.adapters.controllers import api as api_module
from cookie_refresher.adapters.controllers.api import router
from cookie_refresher.domain.entities import AgentResult, SessionCookies


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


COOKIES = SessionCookies(cf_clearance="cf_tok", ci_session="ci_tok")


class TestHealthEndpoint:
    def test_returns_200_with_ok_status(self):
        client = TestClient(_make_test_app())
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["service"] == "cookie-refresher"


class TestRefreshEndpoint:
    def test_returns_503_when_use_case_not_initialised(self):
        api_module._use_case_factory = None
        client = TestClient(_make_test_app())
        response = client.post("/refresh")
        assert response.status_code == 503

    def test_returns_success_result_from_use_case(self):
        mock_use_case = AsyncMock()
        mock_use_case.execute.return_value = AgentResult.ok(COOKIES, steps_taken=4)
        api_module.set_use_case_factory(lambda: mock_use_case)

        client = TestClient(_make_test_app())
        response = client.post("/refresh")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["steps_taken"] == 4
        assert body["error"] is None

    def test_returns_failure_result_from_use_case(self):
        mock_use_case = AsyncMock()
        mock_use_case.execute.return_value = AgentResult.fail(
            "Max steps exceeded", steps_taken=20
        )
        api_module.set_use_case_factory(lambda: mock_use_case)

        client = TestClient(_make_test_app())
        response = client.post("/refresh")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["error"] == "Max steps exceeded"

    def teardown_method(self):
        api_module._use_case_factory = None
