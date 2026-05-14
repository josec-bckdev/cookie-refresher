"""RED: ReplaySessionUseCase behaviour — verified through port mocks."""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from cookie_refresher.domain.entities import (
    ActionScript,
    AgentStep,
    RecordedStep,
    SessionCookies,
)
from cookie_refresher.domain.ports import IBrowserGateway, IVtrackGateway, IAgentClient, IActionScriptStore
from cookie_refresher.application.use_cases.replay_session import ReplaySessionUseCase


COOKIES = SessionCookies(cf_clearance="cf_tok", ci_session="ci_tok")
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

SCRIPT = ActionScript(
    steps=[
        RecordedStep("left_click", {"coordinate": [100, 200]}, 50.0),
        RecordedStep("type", {"text": "{{email}}"}, 30.0),
        RecordedStep("type", {"text": "{{password}}"}, 30.0),
        RecordedStep("left_click", {"coordinate": [300, 400]}, 50.0),
    ],
    recorded_at=datetime(2026, 5, 13, 12, 0, 0),
    use_count=0,
)


def _make_browser() -> IBrowserGateway:
    browser = AsyncMock(spec=IBrowserGateway)
    browser.take_screenshot.return_value = FAKE_PNG
    return browser


def _make_vtrack(success: bool = True) -> IVtrackGateway:
    vtrack = AsyncMock(spec=IVtrackGateway)
    vtrack.post_cookies.return_value = success
    return vtrack


def _make_agent_done() -> IAgentClient:
    agent = AsyncMock(spec=IAgentClient)
    agent.complete.return_value = AgentStep(
        actions=[], is_done=True, cookies=COOKIES, reasoning="Cookies found."
    )
    return agent


def _make_use_case(browser=None, vtrack=None, agent=None, script=None, script_store=None, randomness_pct=0.0):
    return ReplaySessionUseCase(
        browser=browser or _make_browser(),
        vtrack=vtrack or _make_vtrack(),
        agent=agent or _make_agent_done(),
        script=script or SCRIPT,
        login_url="https://example.com/login",
        login_email="user@x.com",
        login_password="secret",
        randomness_pct=randomness_pct,
        script_store=script_store,
    )


@pytest.mark.asyncio
class TestReplaySessionUseCase:
    async def test_dispatches_each_recorded_step(self):
        browser = _make_browser()
        use_case = _make_use_case(browser=browser)

        with patch("asyncio.sleep"):
            await use_case.execute()

        browser.click.assert_awaited()
        browser.type_text.assert_awaited()

    async def test_resolves_email_sentinel(self):
        browser = _make_browser()
        use_case = _make_use_case(browser=browser, randomness_pct=0.0)

        with patch("asyncio.sleep"):
            await use_case.execute()

        type_calls = [call.args[0] for call in browser.type_text.await_args_list]
        assert "user@x.com" in type_calls

    async def test_resolves_password_sentinel(self):
        browser = _make_browser()
        use_case = _make_use_case(browser=browser, randomness_pct=0.0)

        with patch("asyncio.sleep"):
            await use_case.execute()

        type_calls = [call.args[0] for call in browser.type_text.await_args_list]
        assert "secret" in type_calls

    async def test_makes_exactly_one_agent_call(self):
        agent = _make_agent_done()
        use_case = _make_use_case(agent=agent)

        with patch("asyncio.sleep"):
            await use_case.execute()

        agent.complete.assert_awaited_once()

    async def test_success_posts_cookies_to_vtrack(self):
        vtrack = _make_vtrack(success=True)
        use_case = _make_use_case(vtrack=vtrack)

        with patch("asyncio.sleep"):
            await use_case.execute()

        vtrack.post_cookies.assert_awaited_once_with(COOKIES)

    async def test_returns_success_result(self):
        use_case = _make_use_case()

        with patch("asyncio.sleep"):
            result = await use_case.execute()

        assert result.success is True
        assert result.cookies == COOKIES

    async def test_failure_when_vtrack_rejects(self):
        vtrack = _make_vtrack(success=False)
        use_case = _make_use_case(vtrack=vtrack)

        with patch("asyncio.sleep"):
            result = await use_case.execute()

        assert result.success is False
        assert "vtrack" in result.error.lower()

    async def test_failure_when_agent_returns_no_cookies(self):
        agent = AsyncMock(spec=IAgentClient)
        agent.complete.return_value = AgentStep(
            actions=[], is_done=True, cookies=None, reasoning="Could not find cookies."
        )
        use_case = _make_use_case(agent=agent)

        with patch("asyncio.sleep"):
            result = await use_case.execute()

        assert result.success is False

    async def test_step_count_includes_script_steps_plus_agent_call(self):
        use_case = _make_use_case()

        with patch("asyncio.sleep"):
            result = await use_case.execute()

        assert result.steps_taken == len(SCRIPT.steps) + 1

    async def test_navigates_to_login_url(self):
        browser = _make_browser()
        use_case = _make_use_case(browser=browser)

        with patch("asyncio.sleep"):
            await use_case.execute()

        browser.navigate.assert_awaited_once()
        args = browser.navigate.await_args
        assert "example.com" in args[0][0]

    async def test_increments_use_count_on_success(self):
        script = ActionScript(
            steps=[RecordedStep("left_click", {"coordinate": [100, 200]}, 50.0)],
            recorded_at=datetime(2026, 5, 13, 12, 0, 0),
            use_count=2,
        )
        script_store = AsyncMock(spec=IActionScriptStore)
        use_case = _make_use_case(script=script, script_store=script_store)

        with patch("asyncio.sleep"):
            await use_case.execute()

        assert script.use_count == 3
        script_store.save.assert_awaited_once()

    async def test_does_not_increment_use_count_on_failure(self):
        script = ActionScript(
            steps=[RecordedStep("left_click", {"coordinate": [100, 200]}, 50.0)],
            recorded_at=datetime(2026, 5, 13, 12, 0, 0),
            use_count=0,
        )
        script_store = AsyncMock(spec=IActionScriptStore)
        vtrack = _make_vtrack(success=False)
        use_case = _make_use_case(script=script, script_store=script_store, vtrack=vtrack)

        with patch("asyncio.sleep"):
            await use_case.execute()

        assert script.use_count == 0
        script_store.save.assert_not_awaited()

    async def test_jitter_applies_nonzero_sleep(self):
        """With randomness_pct > 0, sleep is still called for each step."""
        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)

        use_case = _make_use_case(randomness_pct=0.20)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await use_case.execute()

        assert len(sleep_calls) == len(SCRIPT.steps)
        assert all(s >= 0 for s in sleep_calls)

    async def test_takes_screenshot_after_each_step(self):
        """One screenshot per step (for flow debugging) plus one final for cookie extraction."""
        browser = _make_browser()
        use_case = _make_use_case(browser=browser)

        with patch("asyncio.sleep"):
            await use_case.execute()

        assert browser.take_screenshot.await_count == len(SCRIPT.steps) + 1

    async def test_step_screenshot_taken_after_sleep(self):
        """Screenshots capture state after the action has had time to settle."""
        call_order = []

        async def record_screenshot():
            call_order.append("screenshot")
            return FAKE_PNG

        async def record_sleep(_):
            call_order.append("sleep")

        browser = _make_browser()
        browser.take_screenshot.side_effect = record_screenshot
        use_case = _make_use_case(browser=browser)

        with patch("asyncio.sleep", side_effect=record_sleep):
            await use_case.execute()

        # For each script step: sleep then screenshot
        for i in range(len(SCRIPT.steps)):
            assert call_order[i * 2] == "sleep", f"step {i}: expected sleep first"
            assert call_order[i * 2 + 1] == "screenshot", f"step {i}: expected screenshot after sleep"
