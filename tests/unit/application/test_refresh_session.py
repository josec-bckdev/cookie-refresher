"""RED: use-case behaviour verified through port mocks — zero real I/O."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from cookie_refresher.domain.entities import SessionCookies, AgentResult
from cookie_refresher.domain.ports import IBrowserGateway, IVtrackGateway, IAgentClient
from cookie_refresher.application.use_cases.refresh_session import RefreshSessionUseCase


class TestRedact:
    def test_replaces_email_in_text_block(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "Email: user@x.com here"}]}]
        result = RefreshSessionUseCase._redact(msgs, "user@x.com", "secret")
        assert "user@x.com" not in result[0]["content"][0]["text"]
        assert "[REDACTED]" in result[0]["content"][0]["text"]

    def test_replaces_password_in_text_block(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "Password: secret here"}]}]
        result = RefreshSessionUseCase._redact(msgs, "user@x.com", "secret")
        assert "secret" not in result[0]["content"][0]["text"]
        assert "[REDACTED]" in result[0]["content"][0]["text"]

    def test_non_text_blocks_untouched(self):
        img = {"type": "image", "source": {"data": "abc123"}}
        msgs = [{"role": "user", "content": [img]}]
        result = RefreshSessionUseCase._redact(msgs, "user@x.com", "secret")
        assert result[0]["content"][0] == img

    def test_returns_independent_copy(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "Email: user@x.com"}]}]
        result = RefreshSessionUseCase._redact(msgs, "user@x.com", "secret")
        assert msgs[0]["content"][0]["text"] == "Email: user@x.com"  # original unchanged


COOKIES = SessionCookies(cf_clearance="cf_tok", ci_session="ci_tok")
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def _make_browser() -> IBrowserGateway:
    browser = AsyncMock(spec=IBrowserGateway)
    browser.take_screenshot.return_value = FAKE_PNG
    return browser


def _make_vtrack(success: bool = True) -> IVtrackGateway:
    vtrack = AsyncMock(spec=IVtrackGateway)
    vtrack.post_cookies.return_value = success
    return vtrack


def _make_agent_done_immediately() -> IAgentClient:
    """Agent that signals completion on the very first step."""
    agent = AsyncMock(spec=IAgentClient)
    from cookie_refresher.domain.entities import AgentStep
    agent.complete.return_value = AgentStep(
        actions=[], is_done=True, cookies=COOKIES, reasoning="Cookies found."
    )
    return agent


def _make_agent_never_done(n_steps: int = 5) -> IAgentClient:
    """Agent that always requests more actions — simulates timeout."""
    agent = AsyncMock(spec=IAgentClient)
    from cookie_refresher.domain.entities import AgentStep, ActionRequest
    action = ActionRequest(action_type="screenshot", params={}, tool_use_id="tid_1")
    agent.complete.return_value = AgentStep(
        actions=[action], is_done=False, cookies=None, reasoning="Still looking..."
    )
    return agent


@pytest.mark.asyncio
class TestRefreshSessionUseCase:
    async def test_success_flow_navigates_then_loops(self):
        browser = _make_browser()
        vtrack = _make_vtrack(success=True)
        agent = _make_agent_done_immediately()

        use_case = RefreshSessionUseCase(browser=browser, vtrack=vtrack, agent=agent, login_email="u@x.com", login_password="pw")
        result = await use_case.execute()

        browser.navigate.assert_awaited_once()
        assert result.success is True
        assert result.cookies == COOKIES

    async def test_success_posts_cookies_to_vtrack(self):
        browser = _make_browser()
        vtrack = _make_vtrack(success=True)
        agent = _make_agent_done_immediately()

        use_case = RefreshSessionUseCase(browser=browser, vtrack=vtrack, agent=agent, login_email="u@x.com", login_password="pw")
        await use_case.execute()

        vtrack.post_cookies.assert_awaited_once_with(COOKIES)

    async def test_failure_when_vtrack_post_fails(self):
        browser = _make_browser()
        vtrack = _make_vtrack(success=False)
        agent = _make_agent_done_immediately()

        use_case = RefreshSessionUseCase(browser=browser, vtrack=vtrack, agent=agent, login_email="u@x.com", login_password="pw")
        result = await use_case.execute()

        assert result.success is False
        assert "vtrack" in result.error.lower()

    async def test_failure_when_max_steps_exceeded(self):
        browser = _make_browser()
        vtrack = _make_vtrack()
        agent = _make_agent_never_done()

        use_case = RefreshSessionUseCase(
            browser=browser, vtrack=vtrack, agent=agent, login_email="u@x.com", login_password="pw", max_steps=3
        )
        result = await use_case.execute()

        assert result.success is False
        assert result.steps_taken == 3
        vtrack.post_cookies.assert_not_awaited()

    async def test_step_counter_is_accurate(self):
        browser = _make_browser()
        vtrack = _make_vtrack()
        agent = _make_agent_done_immediately()

        use_case = RefreshSessionUseCase(browser=browser, vtrack=vtrack, agent=agent, login_email="u@x.com", login_password="pw")
        result = await use_case.execute()

        assert result.steps_taken == 1

    async def test_screenshot_taken_at_start_of_each_step(self):
        browser = _make_browser()
        vtrack = _make_vtrack()

        # Agent takes 2 action steps before reporting done
        agent = AsyncMock(spec=IAgentClient)
        from cookie_refresher.domain.entities import AgentStep, ActionRequest
        call_count = 0

        async def side_effect(messages):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                action = ActionRequest(
                    action_type="left_click",
                    params={"coordinate": [100, 200]},
                    tool_use_id=f"tid_{call_count}",
                )
                return AgentStep(actions=[action], is_done=False, cookies=None, reasoning="")
            return AgentStep(actions=[], is_done=True, cookies=COOKIES, reasoning="Done")

        agent.complete.side_effect = side_effect

        use_case = RefreshSessionUseCase(browser=browser, vtrack=vtrack, agent=agent, login_email="u@x.com", login_password="pw")
        result = await use_case.execute()

        assert browser.take_screenshot.await_count == 3
        assert result.steps_taken == 3
