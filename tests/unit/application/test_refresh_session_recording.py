"""RED: action recording behaviour in RefreshSessionUseCase."""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from cookie_refresher.domain.entities import (
    ActionScript,
    AgentStep,
    ActionRequest,
    RecordedStep,
    SessionCookies,
)
from cookie_refresher.domain.ports import IBrowserGateway, IVtrackGateway, IAgentClient, IActionScriptStore
from cookie_refresher.application.use_cases.refresh_session import RefreshSessionUseCase


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
    agent = AsyncMock(spec=IAgentClient)
    agent.complete.return_value = AgentStep(
        actions=[], is_done=True, cookies=COOKIES, reasoning="Done."
    )
    return agent


def _make_use_case(script_store=None, max_inter_step_ms=3000.0, agent=None, vtrack=None, browser=None):
    return RefreshSessionUseCase(
        browser=browser or _make_browser(),
        vtrack=vtrack or _make_vtrack(),
        agent=agent or _make_agent_done_immediately(),
        login_email="user@x.com",
        login_password="secret",
        script_store=script_store,
        max_inter_step_ms=max_inter_step_ms,
    )


class TestMaskCredentials:
    def test_masks_email(self):
        uc = _make_use_case()
        result = uc._mask_credentials({"text": "user@x.com"}, "type")
        assert result["text"] == "{{email}}"

    def test_masks_password(self):
        uc = _make_use_case()
        result = uc._mask_credentials({"text": "secret"}, "type")
        assert result["text"] == "{{password}}"

    def test_non_type_action_unchanged(self):
        uc = _make_use_case()
        params = {"coordinate": [100, 200]}
        result = uc._mask_credentials(params, "left_click")
        assert result == params

    def test_type_action_with_other_text_unchanged(self):
        uc = _make_use_case()
        params = {"text": "something else"}
        result = uc._mask_credentials(params, "type")
        assert result == params


@pytest.mark.asyncio
class TestRecordingInRunLoop:
    async def test_saves_script_on_success(self):
        script_store = AsyncMock(spec=IActionScriptStore)
        agent = AsyncMock(spec=IAgentClient)
        action = ActionRequest(action_type="left_click", params={"coordinate": [100, 200]}, tool_use_id="t1")

        call_count = 0

        async def side_effect(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return AgentStep(actions=[action], is_done=False, cookies=None, reasoning="")
            return AgentStep(actions=[], is_done=True, cookies=COOKIES, reasoning="Done")

        agent.complete.side_effect = side_effect
        uc = _make_use_case(script_store=script_store, agent=agent)

        result = await uc.execute()

        assert result.success is True
        script_store.save.assert_awaited_once()
        saved_script: ActionScript = script_store.save.await_args[0][0]
        assert isinstance(saved_script, ActionScript)
        assert len(saved_script.steps) == 1
        assert saved_script.steps[0].action_type == "left_click"

    async def test_does_not_save_on_failure(self):
        script_store = AsyncMock(spec=IActionScriptStore)
        vtrack = _make_vtrack(success=False)
        uc = _make_use_case(script_store=script_store, vtrack=vtrack)

        result = await uc.execute()

        assert result.success is False
        script_store.save.assert_not_awaited()

    async def test_screenshot_actions_excluded_from_recording(self):
        script_store = AsyncMock(spec=IActionScriptStore)
        agent = AsyncMock(spec=IAgentClient)
        screenshot_action = ActionRequest(action_type="screenshot", params={}, tool_use_id="t1")
        click_action = ActionRequest(action_type="left_click", params={"coordinate": [100, 200]}, tool_use_id="t2")

        call_count = 0

        async def side_effect(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return AgentStep(actions=[screenshot_action, click_action], is_done=False, cookies=None, reasoning="")
            return AgentStep(actions=[], is_done=True, cookies=COOKIES, reasoning="Done")

        agent.complete.side_effect = side_effect
        uc = _make_use_case(script_store=script_store, agent=agent)

        await uc.execute()

        saved_script: ActionScript = script_store.save.await_args[0][0]
        action_types = [s.action_type for s in saved_script.steps]
        assert "screenshot" not in action_types
        assert "left_click" in action_types

    async def test_credentials_masked_in_recorded_script(self):
        script_store = AsyncMock(spec=IActionScriptStore)
        agent = AsyncMock(spec=IAgentClient)
        type_email = ActionRequest(action_type="type", params={"text": "user@x.com"}, tool_use_id="t1")
        type_pass = ActionRequest(action_type="type", params={"text": "secret"}, tool_use_id="t2")

        call_count = 0

        async def side_effect(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return AgentStep(actions=[type_email, type_pass], is_done=False, cookies=None, reasoning="")
            return AgentStep(actions=[], is_done=True, cookies=COOKIES, reasoning="Done")

        agent.complete.side_effect = side_effect
        uc = _make_use_case(script_store=script_store, agent=agent)

        await uc.execute()

        saved_script: ActionScript = script_store.save.await_args[0][0]
        texts = [s.params.get("text") for s in saved_script.steps]
        assert "user@x.com" not in texts
        assert "secret" not in texts
        assert "{{email}}" in texts
        assert "{{password}}" in texts

    async def test_no_script_store_runs_without_error(self):
        uc = _make_use_case(script_store=None)
        result = await uc.execute()
        assert result.success is True

    async def test_recorded_steps_have_delay_after_ms(self):
        script_store = AsyncMock(spec=IActionScriptStore)
        agent = AsyncMock(spec=IAgentClient)
        action = ActionRequest(action_type="left_click", params={"coordinate": [100, 200]}, tool_use_id="t1")

        call_count = 0

        async def side_effect(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return AgentStep(actions=[action], is_done=False, cookies=None, reasoning="")
            return AgentStep(actions=[], is_done=True, cookies=COOKIES, reasoning="Done")

        agent.complete.side_effect = side_effect
        uc = _make_use_case(script_store=script_store, agent=agent)

        await uc.execute()

        saved_script: ActionScript = script_store.save.await_args[0][0]
        assert saved_script.steps[0].delay_after_ms >= 0.0
