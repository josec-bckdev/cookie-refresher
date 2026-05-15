"""RED: NoAgentStepsUseCase behaviour — zero AI calls, direct cookie read."""
import pytest
from unittest.mock import AsyncMock, patch
from cookie_refresher.domain.entities import (
    ProgrammedScript, ProgrammedStep, RunMode, SessionCookies,
)
from cookie_refresher.domain.ports import IBrowserGateway, IVtrackGateway
from cookie_refresher.application.use_cases.no_agent_steps import NoAgentStepsUseCase

COOKIES = SessionCookies(cf_clearance="cf_tok", ci_session="ci_tok")

SCRIPT = ProgrammedScript(steps=[
    ProgrammedStep("left_click", {"coordinate": [642, 470]}, delay_after_ms=500.0),
    ProgrammedStep("type", {"text": "{{password}}"}, delay_after_ms=300.0),
    ProgrammedStep("left_click", {"coordinate": [759, 519]}, delay_after_ms=8000.0),
    ProgrammedStep("get_cookies", {"names": ["cf_clearance", "ci_session"]}),
])

SCRIPT_NO_DELAY = ProgrammedScript(steps=[
    ProgrammedStep("left_click", {"coordinate": [100, 200]}),  # delay=0.0
    ProgrammedStep("get_cookies", {"names": ["cf_clearance", "ci_session"]}),
])


def _make_browser(cookies=COOKIES):
    browser = AsyncMock(spec=IBrowserGateway)
    browser.get_cookies.return_value = cookies
    return browser


def _make_vtrack(success=True):
    vtrack = AsyncMock(spec=IVtrackGateway)
    vtrack.post_cookies.return_value = success
    return vtrack


def _make_use_case(browser=None, vtrack=None, script=None, randomness_pct=0.0):
    return NoAgentStepsUseCase(
        browser=browser or _make_browser(),
        vtrack=vtrack or _make_vtrack(),
        script=script or SCRIPT,
        login_url="https://example.com/login",
        login_email="user@x.com",
        login_password="secret",
        randomness_pct=randomness_pct,
    )


@pytest.mark.asyncio
class TestNoAgentStepsUseCase:
    async def test_navigates_to_login_url(self):
        browser = _make_browser()
        use_case = _make_use_case(browser=browser)
        with patch("asyncio.sleep"):
            await use_case.execute()
        browser.navigate.assert_awaited_once()
        assert "example.com" in browser.navigate.await_args[0][0]

    async def test_dispatches_non_terminal_steps(self):
        browser = _make_browser()
        use_case = _make_use_case(browser=browser)
        with patch("asyncio.sleep"):
            await use_case.execute()
        browser.click.assert_awaited()

    async def test_resolves_password_sentinel(self):
        browser = _make_browser()
        use_case = _make_use_case(browser=browser, randomness_pct=0.0)
        with patch("asyncio.sleep"):
            await use_case.execute()
        type_calls = [call.args[0] for call in browser.type_text.await_args_list]
        assert "secret" in type_calls

    async def test_calls_get_cookies_with_correct_names(self):
        browser = _make_browser()
        use_case = _make_use_case(browser=browser)
        with patch("asyncio.sleep"):
            await use_case.execute()
        browser.get_cookies.assert_awaited_once_with(["cf_clearance", "ci_session"])

    async def test_success_posts_cookies_to_vtrack(self):
        vtrack = _make_vtrack(success=True)
        use_case = _make_use_case(vtrack=vtrack)
        with patch("asyncio.sleep"):
            await use_case.execute()
        vtrack.post_cookies.assert_awaited_once_with(COOKIES)

    async def test_returns_success_with_programmed_mode(self):
        use_case = _make_use_case()
        with patch("asyncio.sleep"):
            result = await use_case.execute()
        assert result.success is True
        assert result.mode == RunMode.PROGRAMMED
        assert result.cookies == COOKIES

    async def test_failure_when_vtrack_rejects(self):
        vtrack = _make_vtrack(success=False)
        use_case = _make_use_case(vtrack=vtrack)
        with patch("asyncio.sleep"):
            result = await use_case.execute()
        assert result.success is False
        assert "vtrack" in result.error.lower()

    async def test_failure_when_get_cookies_raises(self):
        browser = _make_browser()
        browser.get_cookies.side_effect = ValueError("cf_clearance cannot be empty")
        use_case = _make_use_case(browser=browser)
        with patch("asyncio.sleep"):
            result = await use_case.execute()
        assert result.success is False

    async def test_step_count_includes_all_steps(self):
        use_case = _make_use_case()
        with patch("asyncio.sleep"):
            result = await use_case.execute()
        assert result.steps_taken == len(SCRIPT.steps)

    async def test_stops_after_terminal_step(self):
        """Steps defined after get_cookies are never executed."""
        script = ProgrammedScript(steps=[
            ProgrammedStep("get_cookies", {"names": ["cf_clearance", "ci_session"]}),
            ProgrammedStep("left_click", {"coordinate": [999, 999]}),
        ])
        browser = _make_browser()
        use_case = _make_use_case(browser=browser, script=script)
        with patch("asyncio.sleep"):
            await use_case.execute()
        browser.click.assert_not_awaited()

    async def test_sleep_not_called_when_delay_is_zero(self):
        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)

        use_case = _make_use_case(script=SCRIPT_NO_DELAY)
        with patch("asyncio.sleep", side_effect=fake_sleep):
            await use_case.execute()
        assert sleep_calls == []

    async def test_no_agent_client_in_constructor(self):
        """NoAgentStepsUseCase must not accept or require an IAgentClient."""
        import inspect
        sig = inspect.signature(NoAgentStepsUseCase.__init__)
        assert "agent" not in sig.parameters
