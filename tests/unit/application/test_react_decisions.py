"""RED: verifies the action-dispatch table in the ReAct loop."""
import pytest
from unittest.mock import AsyncMock
from cookie_refresher.domain.ports import IBrowserGateway
from cookie_refresher.application.use_cases.refresh_session import ActionDispatcher


FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50


@pytest.fixture
def browser() -> IBrowserGateway:
    b = AsyncMock(spec=IBrowserGateway)
    b.take_screenshot.return_value = FAKE_PNG
    return b


@pytest.mark.asyncio
class TestActionDispatcher:
    async def test_screenshot_returns_image_content(self, browser):
        dispatcher = ActionDispatcher(browser)
        result = await dispatcher.dispatch({"action": "screenshot"})
        assert isinstance(result, list)
        assert result[0]["type"] == "image"
        browser.take_screenshot.assert_awaited_once()

    async def test_left_click_calls_browser_click(self, browser):
        dispatcher = ActionDispatcher(browser)
        await dispatcher.dispatch({"action": "left_click", "coordinate": [320, 480]})
        browser.click.assert_awaited_once_with(320, 480)

    async def test_double_click_calls_browser_double_click(self, browser):
        dispatcher = ActionDispatcher(browser)
        await dispatcher.dispatch({"action": "double_click", "coordinate": [100, 200]})
        browser.double_click.assert_awaited_once_with(100, 200)

    async def test_type_calls_browser_type_text(self, browser):
        dispatcher = ActionDispatcher(browser)
        await dispatcher.dispatch({"action": "type", "text": "hello@example.com"})
        browser.type_text.assert_awaited_once_with("hello@example.com")

    async def test_key_calls_browser_press_key(self, browser):
        # Computer Use API sends "text" field for key sequences, not "key"
        dispatcher = ActionDispatcher(browser)
        await dispatcher.dispatch({"action": "key", "text": "Return"})
        browser.press_key.assert_awaited_once_with("Return")

    async def test_scroll_calls_browser_scroll(self, browser):
        # Computer Use API field names
        dispatcher = ActionDispatcher(browser)
        await dispatcher.dispatch({
            "action": "scroll",
            "coordinate": [400, 300],
            "scroll_direction": "down",
            "scroll_distance": 3,
        })
        browser.scroll.assert_awaited_once_with(400, 300, "down", 3)

    async def test_unknown_action_returns_error_string(self, browser):
        dispatcher = ActionDispatcher(browser)
        result = await dispatcher.dispatch({"action": "hover", "coordinate": [0, 0]})
        assert "unknown" in result.lower() or "unsupported" in result.lower()
