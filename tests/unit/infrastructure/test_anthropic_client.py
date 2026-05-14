"""RED: _prune_old_screenshots and agent.claude_call OTel span tests."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from cookie_refresher.infrastructure.anthropic_client import AnthropicAgentClient


def _make_provider() -> tuple[InMemorySpanExporter, TracerProvider]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, provider


def _make_mock_response(input_tokens=1000, output_tokens=200, stop_reason="end_turn", content=None):
    response = MagicMock()
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    response.stop_reason = stop_reason
    response.content = content or []
    return response


def _make_agent(model="claude-test", response=None):
    exporter, provider = _make_provider()
    tracer = provider.get_tracer("test")
    beta_messages = AsyncMock()
    beta_messages.create.return_value = response or _make_mock_response()
    sdk_client = MagicMock()
    sdk_client.beta.messages = beta_messages
    agent = AnthropicAgentClient(client=sdk_client, model=model, tracer=tracer)
    return agent, exporter

_PLACEHOLDER = {"type": "text", "text": "[screenshot omitted]"}


def _img(tag: str = "a") -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": tag}}


def _text(t: str) -> dict:
    return {"type": "text", "text": t}


def _tool_result(tool_use_id: str, content: list) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}


# ── no-op cases ───────────────────────────────────────────────────────────────

def test_empty_messages_unchanged():
    messages = []
    AnthropicAgentClient._prune_old_screenshots(messages)
    assert messages == []


def test_single_image_untouched():
    messages = [{"role": "user", "content": [_text("task"), _img("only")]}]
    AnthropicAgentClient._prune_old_screenshots(messages)
    assert messages[0]["content"][1] == _img("only")


# ── direct image pruning ──────────────────────────────────────────────────────

def test_two_direct_images_keeps_last():
    messages = [
        {"role": "user", "content": [_text("task"), _img("first")]},
        {"role": "assistant", "content": [_text("ok")]},
        {"role": "user", "content": [_img("second")]},
    ]
    AnthropicAgentClient._prune_old_screenshots(messages)
    assert messages[0]["content"][1] == _PLACEHOLDER
    assert messages[2]["content"][0] == _img("second")


def test_three_direct_images_keeps_only_last():
    messages = [
        {"role": "user", "content": [_img("first")]},
        {"role": "user", "content": [_img("second")]},
        {"role": "user", "content": [_img("third")]},
    ]
    AnthropicAgentClient._prune_old_screenshots(messages)
    assert messages[0]["content"][0] == _PLACEHOLDER
    assert messages[1]["content"][0] == _PLACEHOLDER
    assert messages[2]["content"][0] == _img("third")


# ── tool_result image pruning ─────────────────────────────────────────────────

def test_image_inside_tool_result_is_pruned():
    messages = [
        {"role": "user", "content": [_tool_result("t1", [_img("screenshot_via_tool")])]},
        {"role": "user", "content": [_img("latest")]},
    ]
    AnthropicAgentClient._prune_old_screenshots(messages)
    assert messages[0]["content"][0]["content"][0] == _PLACEHOLDER
    assert messages[1]["content"][0] == _img("latest")


# ── mixed sources ─────────────────────────────────────────────────────────────

def test_mix_of_direct_and_tool_result_keeps_last():
    messages = [
        {"role": "user", "content": [_img("direct_old")]},
        {"role": "user", "content": [_tool_result("t1", [_img("tool_old")])]},
        {"role": "user", "content": [_img("latest")]},
    ]
    AnthropicAgentClient._prune_old_screenshots(messages)
    assert messages[0]["content"][0] == _PLACEHOLDER
    assert messages[1]["content"][0]["content"][0] == _PLACEHOLDER
    assert messages[2]["content"][0] == _img("latest")


def test_non_image_content_untouched():
    messages = [
        {"role": "user", "content": [_text("task"), _img("old")]},
        {"role": "assistant", "content": [_text("reasoning")]},
        {"role": "user", "content": [_img("latest")]},
    ]
    AnthropicAgentClient._prune_old_screenshots(messages)
    assert messages[0]["content"][0] == _text("task")
    assert messages[1]["content"][0] == _text("reasoning")


# ── OTel span tests ───────────────────────────────────────────────────────────

class TestAnthropicClientSpan:
    async def test_complete_creates_claude_call_span(self):
        agent, exporter = _make_agent()
        await agent.complete([])
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "agent.claude_call"

    async def test_span_records_model(self):
        agent, exporter = _make_agent(model="claude-opus-4-7")
        await agent.complete([])
        assert exporter.get_finished_spans()[0].attributes["model"] == "claude-opus-4-7"

    async def test_span_records_input_and_output_tokens(self):
        agent, exporter = _make_agent(response=_make_mock_response(input_tokens=5179, output_tokens=612))
        await agent.complete([])
        attrs = exporter.get_finished_spans()[0].attributes
        assert attrs["llm.input_tokens"] == 5179
        assert attrs["llm.output_tokens"] == 612

    async def test_span_records_cache_tokens(self):
        response = _make_mock_response()
        response.usage.cache_read_input_tokens = 300
        response.usage.cache_creation_input_tokens = 100
        agent, exporter = _make_agent(response=response)
        await agent.complete([])
        attrs = exporter.get_finished_spans()[0].attributes
        assert attrs["llm.cache_read_tokens"] == 300
        assert attrs["llm.cache_write_tokens"] == 100

    async def test_span_records_is_done_true_when_cookies_found(self):
        block = MagicMock()
        block.type = "text"
        block.text = 'COOKIES_JSON: {"cf_clearance": "abc", "ci_session": "xyz"}'
        agent, exporter = _make_agent(response=_make_mock_response(content=[block]))
        await agent.complete([])
        assert exporter.get_finished_spans()[0].attributes["agent.is_done"] is True

    async def test_span_records_action_count(self):
        block = MagicMock()
        block.type = "tool_use"
        block.name = "computer"
        block.id = "tool_1"
        block.input = {"action": "left_click", "coordinate": [100, 200]}
        agent, exporter = _make_agent(response=_make_mock_response(content=[block], stop_reason="tool_use"))
        await agent.complete([])
        assert exporter.get_finished_spans()[0].attributes["agent.action_count"] == 1

    async def test_thinking_block_adds_reasoning_event(self):
        block = MagicMock()
        block.type = "thinking"
        block.thinking = "I see the login form and will click the email field."
        agent, exporter = _make_agent(response=_make_mock_response(content=[block]))
        await agent.complete([])
        events = exporter.get_finished_spans()[0].events
        reasoning = [e for e in events if e.name == "agent.reasoning"]
        assert len(reasoning) == 1
        assert "login form" in reasoning[0].attributes["text"]

    async def test_text_block_adds_reasoning_event(self):
        block = MagicMock()
        block.type = "text"
        block.text = "I will click the login button now."
        agent, exporter = _make_agent(response=_make_mock_response(content=[block]))
        await agent.complete([])
        events = exporter.get_finished_spans()[0].events
        reasoning = [e for e in events if e.name == "agent.reasoning"]
        assert len(reasoning) == 1
        assert "login button" in reasoning[0].attributes["text"]

    async def test_tool_use_block_adds_action_event(self):
        block = MagicMock()
        block.type = "tool_use"
        block.name = "computer"
        block.id = "tool_1"
        block.input = {"action": "left_click", "coordinate": [100, 200]}
        agent, exporter = _make_agent(response=_make_mock_response(content=[block], stop_reason="tool_use"))
        await agent.complete([])
        events = exporter.get_finished_spans()[0].events
        actions = [e for e in events if e.name == "agent.action"]
        assert len(actions) == 1
        assert actions[0].attributes["action_type"] == "left_click"
        assert "coordinate" in actions[0].attributes["params"]

    async def test_multiple_blocks_produce_ordered_events(self):
        thinking = MagicMock()
        thinking.type = "thinking"
        thinking.thinking = "I need to click the checkbox."
        action = MagicMock()
        action.type = "tool_use"
        action.name = "computer"
        action.id = "tool_2"
        action.input = {"action": "left_click", "coordinate": [640, 400]}
        agent, exporter = _make_agent(
            response=_make_mock_response(content=[thinking, action], stop_reason="tool_use")
        )
        await agent.complete([])
        events = exporter.get_finished_spans()[0].events
        assert events[0].name == "agent.reasoning"
        assert events[1].name == "agent.action"