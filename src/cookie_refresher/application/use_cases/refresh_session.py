"""
RefreshSessionUseCase — ReAct agentic loop.

Pattern chosen: ReAct (Reason + Act)
  Each iteration: Observe (screenshot) → Think (Claude) → Act (dispatch action).

Why ReAct over alternatives:
  - Plan-and-Execute: pre-commits to a step sequence — brittle against dynamic
    Cloudflare challenges whose UI changes every run.
  - Reflection: adds a self-critique layer. Valuable for code generation; overkill
    here because the screenshot already provides automatic ground truth.
  - ReAct: Claude sees the screen, reasons about the next single action, executes,
    then sees the updated screen. Handles surprises (captchas, popups, UI drift)
    without any special-case code. The logged `Thought:` blocks are the demo.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from cookie_refresher.domain.entities import ActionScript, AgentResult, FailureReason, RecordedStep, RunMode, SessionCookies
from cookie_refresher.domain.ports import IBrowserGateway, IVtrackGateway, IAgentClient, IActionScriptStore

logger = logging.getLogger(__name__)

LOGIN_URL = "https://www.rutasljrj.net/rastreo/ljrj/login"
DEFAULT_MAX_STEPS = 100
_DISPLAY_WIDTH = 1600
_DISPLAY_HEIGHT = 1050


class ActionDispatcher:
    """
    Translates Claude's Computer Use action requests into browser gateway calls.
    Single-responsibility: mapping only. No loop logic here.
    """

    def __init__(self, browser: IBrowserGateway) -> None:
        self._browser = browser

    async def dispatch(self, action: dict) -> object:
        action_type = action.get("action", "")

        if action_type == "screenshot":
            screenshot_bytes = await self._browser.take_screenshot()
            return [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(screenshot_bytes).decode(),
                    },
                }
            ]

        if action_type == "left_click":
            x, y = action["coordinate"]
            await self._browser.click(x, y)
            return "left_click executed"

        if action_type == "double_click":
            x, y = action["coordinate"]
            await self._browser.double_click(x, y)
            return "double_click executed"

        if action_type == "triple_click":
            x, y = action["coordinate"]
            await self._browser.triple_click(x, y)
            return "triple_click executed"

        if action_type == "type":
            await self._browser.type_text(action["text"])
            return "type executed"

        if action_type == "key":
            # Computer Use API uses "text" for key sequences (same field as type)
            await self._browser.press_key(action["text"])
            return "key executed"

        if action_type == "wait":
            duration = action.get("duration", 1000)
            logger.info("Agent requested wait: %dms", duration)
            await asyncio.sleep(duration / 1000)
            return "wait executed"

        if action_type == "scroll":
            x, y = action["coordinate"]
            # Computer Use API uses scroll_direction/scroll_distance
            direction = action.get("scroll_direction", action.get("direction", "down"))
            amount = action.get("scroll_distance", action.get("amount", 3))
            await self._browser.scroll(x, y, direction, amount)
            return "scroll executed"

        if action_type == "right_click":
            x, y = action["coordinate"]
            await self._browser.right_click(x, y)
            return "right_click executed"

        if action_type == "left_click_drag":
            sx, sy = action["start_coordinate"]
            ex, ey = action["coordinate"]
            await self._browser.left_click_drag(sx, sy, ex, ey)
            return "left_click_drag executed"

        logger.warning("Unsupported action type received: %s", action_type)
        return f"unsupported action: {action_type}"


class RefreshSessionUseCase:
    """
    Orchestrates one full session-refresh cycle.

    Responsibilities:
      1. Navigate the browser to the login URL.
      2. Run the ReAct loop: screenshot → agent.complete → dispatch actions.
      3. On done signal: POST cookies to vtrack via IVtrackGateway.
      4. Return AgentResult with outcome and step count (for alerting/logging).

    Does NOT know about Anthropic SDK, httpx, or VNC — those details live in
    the adapter and infrastructure layers.
    """

    def __init__(
        self,
        browser: IBrowserGateway,
        vtrack: IVtrackGateway,
        agent: IAgentClient,
        login_email: str,
        login_password: str,
        max_steps: int = DEFAULT_MAX_STEPS,
        login_url: str = LOGIN_URL,
        script_store: Optional[IActionScriptStore] = None,
        max_inter_step_ms: float = 3000.0,
    ) -> None:
        self._browser = browser
        self._vtrack = vtrack
        self._agent = agent
        self._login_email = login_email
        self._login_password = login_password
        self._max_steps = max_steps
        self._login_url = login_url
        self._script_store = script_store
        self._max_inter_step_ms = max_inter_step_ms
        self._dispatcher = ActionDispatcher(browser)

    async def execute(self) -> AgentResult:
        logger.info("Starting session refresh")
        await self._browser.start()
        try:
            return await self._run_loop()
        finally:
            await self._browser.close()

    async def _run_loop(self) -> AgentResult:
        logger.info("Navigating to %s", self._login_url)
        await self._browser.navigate(self._login_url)

        messages: list[dict] = []
        all_recorded: list[RecordedStep] = []
        t_last_step_end: Optional[float] = None
        step = 0

        while step < self._max_steps:
            screenshot = await self._browser.take_screenshot()
            messages = self._append_screenshot_observation(
                messages, screenshot, self._login_email, self._login_password
            )

            logger.info("ReAct step %d/%d — sending to agent", step + 1, self._max_steps)
            agent_step = await self._agent.complete(messages)

            if agent_step.reasoning:
                logger.info("Agent thought: %s", agent_step.reasoning)

            if agent_step.actions:
                action_types = ", ".join(a.action_type for a in agent_step.actions)
                logger.info("Step %d dispatching: [%s]", step + 1, action_types)

            messages.append(
                {"role": "assistant", "content": self._build_assistant_content(agent_step)}
            )

            step += 1

            if agent_step.is_done:
                result = await self._finalise(agent_step.cookies, step, messages)
                if result.success and self._script_store:
                    script = ActionScript(steps=all_recorded, recorded_at=datetime.now(timezone.utc))
                    await self._script_store.save(script)
                    logger.info("Action script recorded: %d steps", len(all_recorded))
                return result

            # Cap inter-step gap (almost entirely Claude API think time) on last recorded step
            t_actions_start = time.monotonic()
            if t_last_step_end is not None and all_recorded:
                inter_ms = min(
                    (t_actions_start - t_last_step_end) * 1000, self._max_inter_step_ms
                )
                prev = all_recorded[-1]
                all_recorded[-1] = RecordedStep(
                    prev.action_type, prev.params, prev.delay_after_ms + inter_ms
                )

            tool_results, new_steps = await self._execute_and_record(agent_step.actions)
            all_recorded.extend(new_steps)
            t_last_step_end = time.monotonic()
            messages.append({"role": "user", "content": tool_results})

        logger.warning("Max steps (%d) exceeded without completing login", self._max_steps)
        return AgentResult.fail(
            f"Max steps ({self._max_steps}) exceeded without extracting cookies",
            steps_taken=step,
            mode=RunMode.AGENT,
            failure_reason=FailureReason.MAX_STEPS_EXCEEDED,
            messages=self._redact(messages, self._login_email, self._login_password),
        )

    async def _finalise(
        self, cookies: Optional[SessionCookies], steps: int, messages: list
    ) -> AgentResult:
        redacted = self._redact(messages, self._login_email, self._login_password)

        if cookies is None:
            return AgentResult.fail(
                "Agent signalled done but provided no cookies",
                steps_taken=steps,
                mode=RunMode.AGENT,
                failure_reason=FailureReason.NO_COOKIES,
                messages=redacted,
            )

        logger.info("Cookies extracted — posting to vtrack")
        posted = await self._vtrack.post_cookies(cookies)

        if not posted:
            return AgentResult.fail(
                "Cookies extracted but vtrack rejected the POST request",
                steps_taken=steps,
                mode=RunMode.AGENT,
                failure_reason=FailureReason.VTRACK_POST_FAILED,
                messages=redacted,
            )

        logger.info("Session refreshed successfully in %d steps", steps)
        return AgentResult.ok(cookies, steps_taken=steps, mode=RunMode.AGENT, messages=redacted)

    async def _execute_and_record(self, actions) -> tuple[list[dict], list[RecordedStep]]:
        tool_results: list[dict] = []
        recorded: list[RecordedStep] = []
        for action_req in actions:
            t0 = time.monotonic()
            result = await self._dispatcher.dispatch(
                {**action_req.params, "action": action_req.action_type}
            )
            t1 = time.monotonic()
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": action_req.tool_use_id,
                    "content": result if isinstance(result, list) else str(result),
                }
            )
            if action_req.action_type != "screenshot":
                params = self._mask_credentials(action_req.params, action_req.action_type)
                recorded.append(RecordedStep(action_req.action_type, params, (t1 - t0) * 1000))
        return tool_results, recorded

    def _mask_credentials(self, params: dict, action_type: str) -> dict:
        if action_type == "type":
            text = params.get("text", "")
            if text == self._login_email:
                return {**params, "text": "{{email}}"}
            if text == self._login_password:
                return {**params, "text": "{{password}}"}
        return params

    @staticmethod
    def _append_screenshot_observation(
        messages: list[dict],
        screenshot: bytes,
        login_email: str = "",
        login_password: str = "",
    ) -> list[dict]:
        image_content = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(screenshot).decode(),
            },
        }
        if not messages:
            task_text = (
                "You are controlling a browser. Log in and extract two session cookies.\n\n"
                "CREDENTIALS:\n"
                f"  Email: {login_email}\n"
                f"  Password: {login_password}\n\n"
                "── STEP 1: LOGIN ──\n"
                "  a. Click the 'Perfil' dropdown (may say 'Administrador'). Select 'Responsable'.\n"
                "  b. Click the email field and type the email above.\n"
                "  c. Click the password field and type the password above.\n"
                "  d. Click 'Ingresar'.\n"
                "  e. If a Cloudflare checkbox appears, click it and wait for it to clear.\n\n"
                "── STEP 2: OPEN DEVTOOLS ──\n"
                "  a. Click once anywhere on the page to ensure browser focus.\n"
                "  b. Press F12 ONCE. DevTools will open docked to the bottom.\n"
                "  c. Click the ⋮ icon in the TOP-RIGHT of the DevTools panel → 'Undock into\n"
                "     separate window'. DevTools fills its own full window.\n"
                "  d. Click the 'Network' tab.\n"
                "  RULE: Do NOT use the Console tab or type document.cookie — HttpOnly cookies\n"
                "  are invisible there.\n\n"
                "── STEP 3: GO TO NETWORK TAB AND CAPTURE A REQUEST ──\n"
                "  a. Click the 'Network' tab in DevTools.\n"
                "  b. In the Network filter input (top of the Network panel), type 'actualiza'.\n"
                "  c. Press F5 to reload the page — this triggers fresh authenticated requests.\n"
                "     Wait for the page to finish loading.\n"
                "  d. One or more requests named like 'actualiza_valores' will appear in the list.\n"
                "     Click on any one of them.\n\n"
                "── STEP 4: READ COOKIES FROM THE REQUEST HEADERS ──\n"
                "  a. In the right-hand detail panel, click the 'Headers' tab.\n"
                "  b. Scroll down to the 'Request Headers' section.\n"
                "  c. Find the 'cookie:' header. Triple-click its VALUE to select (highlight) the\n"
                "     full text. The selected text will show both cookies:\n"
                "       cf_clearance=<value>; ci_session=<value>\n"
                "  d. READ both values directly from the highlighted text in the screenshot.\n"
                "     Output COOKIES_JSON immediately after reading.\n"
                "  RULES — do NOT do any of the following (they waste steps and lose focus):\n"
                "  • Do NOT press Ctrl+C, Ctrl+V, or paste anywhere.\n"
                "  • Do NOT press Ctrl+= or Ctrl+- to zoom in or out.\n"
                "  • Do NOT navigate away from this header view.\n"
                "  • Do NOT take more screenshots trying to 'improve' the view.\n"
                "  • The selected text IS readable — trust what you see and output it.\n\n"
                "── STEP 5: OUTPUT ──\n"
                "  Output EXACTLY this line with the real values (no truncation, no placeholders):\n"
                'COOKIES_JSON: {"cf_clearance": "<full_value>", "ci_session": "<full_value>"}'
            )
            return [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": task_text}, image_content],
                }
            ]

        # Subsequent turns: screenshot appended as new user content
        updated = list(messages)
        updated.append({"role": "user", "content": [image_content]})
        return updated

    @staticmethod
    def _redact(messages: list, email: str, password: str) -> list:
        """Return a deep copy of messages with email and password replaced."""
        redacted = copy.deepcopy(messages)
        for msg in redacted:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if block.get("type") == "text":
                    block["text"] = block["text"].replace(email, "[REDACTED]").replace(password, "[REDACTED]")
                elif block.get("type") == "tool_use":
                    inp = block.get("input", {})
                    if inp.get("action") == "type" and isinstance(inp.get("text"), str):
                        inp["text"] = inp["text"].replace(email, "[REDACTED]").replace(password, "[REDACTED]")
        return redacted

    @staticmethod
    def _build_assistant_content(agent_step) -> list[dict]:
        content = []
        if agent_step.reasoning:
            content.append({"type": "text", "text": agent_step.reasoning})
        for action in agent_step.actions:
            content.append(
                {
                    "type": "tool_use",
                    "id": action.tool_use_id,
                    "name": "computer",
                    "input": {**action.params, "action": action.action_type},
                }
            )
        return content
