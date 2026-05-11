"""
RED: Integration test for the complete agent login flow.

Mocks the Anthropic SDK at the HTTP boundary (httpx transport) and the
VNC browser gateway at the IBrowserGateway port. The use case, domain
entities, and action dispatcher all run for real — only external I/O is faked.

This is the test you'd demo in an interview to show the full flow.
"""
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from cookie_refresher.domain.entities import SessionCookies, AgentStep, ActionRequest
from cookie_refresher.domain.ports import IBrowserGateway, IVtrackGateway
from cookie_refresher.infrastructure.anthropic_client import AnthropicAgentClient
from cookie_refresher.application.use_cases.refresh_session import RefreshSessionUseCase


FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

EXPECTED_COOKIES = SessionCookies(
    cf_clearance="NjCkl95e1sGaQMK5-1775504603-abc",
    ci_session="tsp9aiaibccvlqs7ku4nib0notg9hn4m",
)


def _scripted_anthropic_response(call_number: int):
    """
    Simulates a 4-step agent sequence:
      1. Click login button
      2. Type email
      3. Type password + submit
      4. Report COOKIES_JSON
    """
    scripts = [
        # Step 1: click the login button
        MagicMock(
            content=[
                MagicMock(
                    type="tool_use",
                    id="tu_1",
                    name="computer",
                    input={"action": "left_click", "coordinate": [640, 450]},
                )
            ]
        ),
        # Step 2: type email
        MagicMock(
            content=[
                MagicMock(
                    type="tool_use",
                    id="tu_2",
                    name="computer",
                    input={"action": "type", "text": "user@example.com"},
                )
            ]
        ),
        # Step 3: type password and submit
        MagicMock(
            content=[
                MagicMock(
                    type="tool_use",
                    id="tu_3",
                    name="computer",
                    input={"action": "key", "text": "Return"},
                )
            ]
        ),
        # Step 4: done — report cookies
        MagicMock(
            content=[
                MagicMock(
                    type="text",
                    text=(
                        "I can see the dashboard. The cookies are ready.\n"
                        f'COOKIES_JSON: {json.dumps({"cf_clearance": EXPECTED_COOKIES.cf_clearance, "ci_session": EXPECTED_COOKIES.ci_session})}'
                    ),
                )
            ]
        ),
    ]
    return scripts[min(call_number, len(scripts) - 1)]


@pytest.mark.asyncio
class TestAgentLoginFlow:
    async def test_full_flow_extracts_and_posts_cookies(self):
        browser = AsyncMock(spec=IBrowserGateway)
        browser.take_screenshot.return_value = FAKE_PNG

        vtrack = AsyncMock(spec=IVtrackGateway)
        vtrack.post_cookies.return_value = True

        call_counter = {"n": 0}

        async def mock_messages_create(**kwargs):
            resp = _scripted_anthropic_response(call_counter["n"])
            call_counter["n"] += 1
            return resp

        mock_client = MagicMock()
        mock_client.beta.messages.create = AsyncMock(side_effect=mock_messages_create)

        agent = AnthropicAgentClient(client=mock_client)
        use_case = RefreshSessionUseCase(browser=browser, vtrack=vtrack, agent=agent, login_email="u@x.com", login_password="pw")

        result = await use_case.execute()

        assert result.success is True
        assert result.cookies == EXPECTED_COOKIES
        assert result.steps_taken == 4
        vtrack.post_cookies.assert_awaited_once_with(EXPECTED_COOKIES)

    async def test_flow_fails_gracefully_when_cookies_not_found(self):
        browser = AsyncMock(spec=IBrowserGateway)
        browser.take_screenshot.return_value = FAKE_PNG

        vtrack = AsyncMock(spec=IVtrackGateway)

        async def always_click(**kwargs):
            return MagicMock(
                content=[
                    MagicMock(
                        type="tool_use",
                        id="tu_loop",
                        name="computer",
                        input={"action": "screenshot"},
                    )
                ]
            )

        mock_client = MagicMock()
        mock_client.beta.messages.create = AsyncMock(side_effect=always_click)

        agent = AnthropicAgentClient(client=mock_client)
        use_case = RefreshSessionUseCase(
            browser=browser, vtrack=vtrack, agent=agent, login_email="u@x.com", login_password="pw", max_steps=5
        )

        result = await use_case.execute()

        assert result.success is False
        assert result.steps_taken == 5
        vtrack.post_cookies.assert_not_awaited()

    async def test_vtrack_post_failure_reported_in_result(self):
        browser = AsyncMock(spec=IBrowserGateway)
        browser.take_screenshot.return_value = FAKE_PNG

        vtrack = AsyncMock(spec=IVtrackGateway)
        vtrack.post_cookies.return_value = False

        call_counter = {"n": 0}

        async def mock_messages_create(**kwargs):
            resp = _scripted_anthropic_response(call_counter["n"])
            call_counter["n"] += 1
            return resp

        mock_client = MagicMock()
        mock_client.beta.messages.create = AsyncMock(side_effect=mock_messages_create)

        agent = AnthropicAgentClient(client=mock_client)
        use_case = RefreshSessionUseCase(browser=browser, vtrack=vtrack, agent=agent, login_email="u@x.com", login_password="pw")

        result = await use_case.execute()

        assert result.success is False
        assert "vtrack" in result.error.lower()
