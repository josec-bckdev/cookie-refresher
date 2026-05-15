"""NoAgentStepsUseCase — zero-AI path that reads cookies directly from the browser."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

from cookie_refresher.domain.entities import (
    AgentResult, FailureReason, ProgrammedScript, RunMode, SessionCookies,
)
from cookie_refresher.domain.ports import IBrowserGateway, IVtrackGateway
from cookie_refresher.application.use_cases.refresh_session import ActionDispatcher

logger = logging.getLogger(__name__)


class NoAgentStepsUseCase:
    def __init__(
        self,
        browser: IBrowserGateway,
        vtrack: IVtrackGateway,
        script: ProgrammedScript,
        login_url: str,
        login_email: str,
        login_password: str,
        randomness_pct: float = 0.0,
    ) -> None:
        self._browser = browser
        self._vtrack = vtrack
        self._script = script
        self._login_url = login_url
        self._login_email = login_email
        self._login_password = login_password
        self._randomness_pct = randomness_pct
        self._dispatcher = ActionDispatcher(browser)

    async def execute(self) -> AgentResult:
        logger.info("Programmed mode: %d steps, zero AI calls", len(self._script.steps))
        await self._browser.start()
        try:
            return await self._run()
        finally:
            await self._browser.close()

    async def _run(self) -> AgentResult:
        await self._browser.navigate(self._login_url)
        cookies: Optional[SessionCookies] = None
        steps_taken = 0

        for step in self._script.steps:
            if step.action_type == "get_cookies":
                names = step.params.get("names", [])
                try:
                    cookies = await self._browser.get_cookies(names)
                except Exception as exc:
                    logger.error("get_cookies failed: %s", exc)
                    return AgentResult.fail(
                        str(exc),
                        steps_taken=steps_taken + 1,
                        mode=RunMode.PROGRAMMED,
                        failure_reason=FailureReason.NO_COOKIES,
                    )
                steps_taken += 1
                break

            params = self._resolve_credentials(step.params, step.action_type)
            await self._dispatcher.dispatch({**params, "action": step.action_type})
            if step.delay_after_ms > 0:
                await asyncio.sleep(self._jitter(step.delay_after_ms) / 1000)
            steps_taken += 1

        return await self._finalise(cookies, steps_taken)

    async def _finalise(self, cookies: Optional[SessionCookies], steps: int) -> AgentResult:
        if cookies is None:
            return AgentResult.fail(
                "No get_cookies step executed",
                steps_taken=steps,
                mode=RunMode.PROGRAMMED,
                failure_reason=FailureReason.NO_COOKIES,
            )
        logger.info("Cookies retrieved — posting to vtrack")
        posted = await self._vtrack.post_cookies(cookies)
        if not posted:
            return AgentResult.fail(
                "Cookies retrieved but vtrack rejected the POST request",
                steps_taken=steps,
                mode=RunMode.PROGRAMMED,
                failure_reason=FailureReason.VTRACK_POST_FAILED,
            )
        logger.info("Programmed session succeeded in %d steps", steps)
        return AgentResult.ok(cookies, steps_taken=steps, mode=RunMode.PROGRAMMED)

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
