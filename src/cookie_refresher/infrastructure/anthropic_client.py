"""
AnthropicAgentClient — implements IAgentClient using the Anthropic SDK.

Bridges between the domain's IAgentClient port and the real Anthropic
Computer Use API. All SDK-specific types are translated into domain
entities at this boundary.

API surface used:
  - Model:      claude-opus-4-7
  - Beta:       computer-use-2025-11-24
  - Tool type:  computer_20251124
  - Thinking:   adaptive (handles Cloudflare challenge reasoning)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import anthropic
from anthropic.resources.beta.messages.messages import AsyncMessages as _AsyncBetaMessages
from opentelemetry import trace
from opentelemetry.trace import Tracer

from cookie_refresher.domain.entities import (
    ActionRequest,
    AgentStep,
    SessionCookies,
)
from cookie_refresher.domain.ports import IAgentClient

logger = logging.getLogger(__name__)

_COOKIES_PATTERN = re.compile(r"COOKIES_JSON:\s*(\{[^}]+\})", re.DOTALL)

_COMPUTER_TOOL = {
    "type": "computer_20251124",
    "name": "computer",
    "display_width_px": 1600,
    "display_height_px": 1050,
}

_SYSTEM_PROMPT = """You are a browser automation agent using Claude Computer Use.

Goal: Log in to rutasljrj.net and extract the authenticated session cookies.

Rules:
- Take one action at a time and wait to see the result.
- If you see a Cloudflare challenge, solve it visually (click the checkbox, etc.).
- Once logged in, open DevTools (F12), undock it into a separate window via the ⋮ menu,
  click the Network tab, click an 'actualiza_valores' request, open its Headers tab, and read
  cf_clearance and ci_session from the 'cookie:' request header.
- Triple-click the cookie: header value to select it, then read both values directly from the
  highlighted text. Output COOKIES_JSON immediately — do NOT copy, paste, or zoom.
- When you have both values, output EXACTLY this on its own line:
  COOKIES_JSON: {"cf_clearance": "<value>", "ci_session": "<value>"}
"""


class AnthropicAgentClient(IAgentClient):
    def __init__(
        self,
        client: Optional[anthropic.AsyncAnthropic] = None,
        model: str = "claude-opus-4-7",
        max_tokens: int = 4096,
        tracer: Optional[Tracer] = None,
    ) -> None:
        _client = client or anthropic.AsyncAnthropic()
        # Resolve the cached_property once so static analysers see the concrete type.
        self._beta_messages: _AsyncBetaMessages = _client.beta.messages
        self._model = model
        self._max_tokens = max_tokens
        self._tracer = tracer or trace.get_tracer(__name__)

    @staticmethod
    def _prune_old_screenshots(messages: list[dict]) -> None:
        """Replace all but the latest screenshot with a text placeholder to cap token cost."""
        _PLACEHOLDER = {"type": "text", "text": "[screenshot omitted]"}
        image_refs: list[tuple] = []

        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for i, block in enumerate(content):
                if block.get("type") == "image":
                    image_refs.append((content, i))
                elif block.get("type") == "tool_result":
                    inner = block.get("content")
                    if isinstance(inner, list):
                        for j, inner_block in enumerate(inner):
                            if inner_block.get("type") == "image":
                                image_refs.append((inner, j))

        for content_list, idx in image_refs[:-1]:
            content_list[idx] = _PLACEHOLDER

    async def complete(self, messages: list[dict]) -> AgentStep:
        self._prune_old_screenshots(messages)
        with self._tracer.start_as_current_span("agent.claude_call") as span:
            span.set_attribute("model", self._model)
            response = await self._beta_messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=_SYSTEM_PROMPT,
                tools=[_COMPUTER_TOOL],
                messages=messages,
                thinking={"type": "adaptive"},
                betas=["computer-use-2025-11-24"],
            )

            u = response.usage
            cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
            span.set_attribute("llm.input_tokens", u.input_tokens)
            span.set_attribute("llm.output_tokens", u.output_tokens)
            span.set_attribute("llm.cache_read_tokens", cache_read)
            span.set_attribute("llm.cache_write_tokens", cache_write)

            logger.info(
                "Agent API usage — input: %d, output: %d, cache_read: %d, cache_write: %d",
                u.input_tokens, u.output_tokens, cache_read, cache_write,
            )
            logger.debug("Anthropic response — stop_reason=%s blocks=%d", response.stop_reason, len(response.content))

            actions: list[ActionRequest] = []
            cookies: Optional[SessionCookies] = None
            reasoning_parts: list[str] = []

            for block in response.content:
                if block.type == "thinking":
                    text = block.thinking or ""
                    reasoning_parts.append(text)
                    span.add_event("agent.reasoning", {"text": text[:1000]})
                elif block.type == "text":
                    reasoning_parts.append(block.text)
                    span.add_event("agent.reasoning", {"text": block.text[:1000]})
                    cookies = self._try_parse_cookies(block.text)
                elif block.type == "tool_use" and block.name == "computer":
                    input_data = block.input or {}
                    action_type = str(input_data.get("action", "unknown"))
                    params = {k: v for k, v in input_data.items() if k != "action"}
                    actions.append(
                        ActionRequest(
                            action_type=action_type,
                            params=params,
                            tool_use_id=block.id,
                        )
                    )
                    span.add_event("agent.action", {
                        "action_type": action_type,
                        "params": json.dumps(params),
                    })

            is_done = bool(cookies) or (response.stop_reason == "end_turn" and not actions)
            span.set_attribute("agent.is_done", is_done)
            span.set_attribute("agent.action_count", len(actions))

            return AgentStep(
                actions=actions,
                is_done=is_done,
                cookies=cookies,
                reasoning=" | ".join(filter(None, reasoning_parts))[:500],
            )

    @staticmethod
    def _try_parse_cookies(text: str) -> Optional[SessionCookies]:
        match = _COOKIES_PATTERN.search(text)
        if not match:
            return None
        try:
            data = json.loads(match.group(1))
            return SessionCookies(
                cf_clearance=data["cf_clearance"],
                ci_session=data["ci_session"],
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("COOKIES_JSON found but failed to parse: %s", exc)
            return None
