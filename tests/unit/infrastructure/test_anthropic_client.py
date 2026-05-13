"""RED: _prune_old_screenshots replaces all but the latest image with a text placeholder."""
import pytest
from cookie_refresher.infrastructure.anthropic_client import AnthropicAgentClient

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