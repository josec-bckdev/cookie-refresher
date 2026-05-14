"""ReplaySessionUseCase — fast path that replays a recorded action script."""
from __future__ import annotations

import asyncio
import base64
import logging
import random
from typing import Optional

from cookie_refresher.domain.entities import ActionScript, AgentResult, FailureReason, RunMode, SessionCookies
from cookie_refresher.domain.ports import IBrowserGateway, IVtrackGateway, IAgentClient, IActionScriptStore
from cookie_refresher.application.use_cases.refresh_session import ActionDispatcher

logger = logging.getLogger(__name__)


class ReplaySessionUseCase:
    """
    Replays a recorded action script and makes a single Claude API call at the
    end to extract cookies from the final screenshot.

    Reduces per-run cost from ~30 Claude API calls down to 1.
    """

    def __init__(
        self,
        browser: IBrowserGateway,
        vtrack: IVtrackGateway,
        agent: IAgentClient,
        script: ActionScript,
        login_url: str,
        login_email: str,
        login_password: str,
        randomness_pct: float = 0.20,
        script_store: Optional[IActionScriptStore] = None,
    ) -> None:
        self._browser = browser
        self._vtrack = vtrack
        self._agent = agent
        self._script = script
        self._login_url = login_url
        self._login_email = login_email
        self._login_password = login_password
        self._randomness_pct = randomness_pct
        self._script_store = script_store
        self._dispatcher = ActionDispatcher(browser)

    async def execute(self) -> AgentResult:
        logger.info("Replay mode: %d recorded steps", len(self._script.steps))
        await self._browser.start()
        try:
            return await self._run_replay()
        finally:
            await self._browser.close()

    async def _run_replay(self) -> AgentResult:
        await self._browser.navigate(self._login_url)

        for step in self._script.steps:
            params = self._resolve_credentials(step.params, step.action_type)
            await self._dispatcher.dispatch({**params, "action": step.action_type})
            delay_s = self._jitter(step.delay_after_ms) / 1000
            await asyncio.sleep(delay_s)

        screenshot = await self._browser.take_screenshot()
        messages = self._build_extract_message(screenshot)

        logger.info("Replay complete — sending final screenshot to agent for cookie extraction")
        agent_step = await self._agent.complete(messages)

        steps_taken = len(self._script.steps) + 1
        result = await self._finalise(agent_step.cookies, steps_taken)

        if result.success and self._script_store:
            self._script.use_count += 1
            await self._script_store.save(self._script)
            logger.info("Script use count updated to %d", self._script.use_count)

        return result

    async def _finalise(self, cookies: Optional[SessionCookies], steps: int) -> AgentResult:
        if cookies is None:
            return AgentResult.fail(
                "Agent signalled done but provided no cookies",
                steps_taken=steps,
                mode=RunMode.REPLAY,
                failure_reason=FailureReason.NO_COOKIES,
            )

        logger.info("Cookies extracted — posting to vtrack")
        posted = await self._vtrack.post_cookies(cookies)

        if not posted:
            return AgentResult.fail(
                "Cookies extracted but vtrack rejected the POST request",
                steps_taken=steps,
                mode=RunMode.REPLAY,
                failure_reason=FailureReason.VTRACK_POST_FAILED,
            )

        logger.info("Session replayed successfully in %d steps", steps)
        return AgentResult.ok(cookies, steps_taken=steps, mode=RunMode.REPLAY)

    def _resolve_credentials(self, params: dict, action_type: str) -> dict:
        if action_type == "type":
            text = params.get("text", "")
            if text == "{{email}}":
                return {**params, "text": self._login_email}
            if text == "{{password}}":
                return {**params, "text": self._login_password}
        return params

    def _jitter(self, ms: float) -> float:
        factor = 1.0 + random.uniform(-self._randomness_pct, self._randomness_pct)
        return max(0.0, ms * factor)

    @staticmethod
    def _build_extract_message(screenshot: bytes) -> list[dict]:
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "The browser login flow is complete and DevTools is open on the Network tab.\n"
                            "Find the 'cookie:' request header in the visible request headers panel.\n"
                            "Triple-click its value to select it, then read both cookie values and output:\n"
                            'COOKIES_JSON: {"cf_clearance": "<value>", "ci_session": "<value>"}'
                        ),
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(screenshot).decode(),
                        },
                    },
                ],
            }
        ]
