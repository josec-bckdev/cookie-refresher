"""RED: IActionScriptStore port contract."""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock
from cookie_refresher.domain.ports import IActionScriptStore, IBrowserGateway, IProgrammedScriptStore
from cookie_refresher.domain.entities import (
    ActionScript, RecordedStep, ProgrammedScript, ProgrammedStep, SessionCookies,
)


class TestIActionScriptStore:
    def test_port_is_abstract(self):
        with pytest.raises(TypeError):
            IActionScriptStore()  # type: ignore[abstract]

    def test_save_is_abstract(self):
        assert hasattr(IActionScriptStore, "save")

    def test_load_is_abstract(self):
        assert hasattr(IActionScriptStore, "load")

    @pytest.mark.asyncio
    async def test_concrete_implementation_can_save_and_load(self):
        """A conforming concrete class must implement both methods."""

        class FakeStore(IActionScriptStore):
            def __init__(self):
                self._stored = None

            async def save(self, script: ActionScript) -> None:
                self._stored = script

            async def load(self) -> ActionScript | None:
                return self._stored

        step = RecordedStep("left_click", {"coordinate": [100, 200]}, 500.0)
        script = ActionScript(steps=[step], recorded_at=datetime(2026, 5, 13, 12, 0, 0))

        store = FakeStore()
        await store.save(script)
        loaded = await store.load()

        assert loaded is script

    @pytest.mark.asyncio
    async def test_load_returns_none_when_empty(self):
        class FakeStore(IActionScriptStore):
            async def save(self, script: ActionScript) -> None:
                pass

            async def load(self) -> ActionScript | None:
                return None

        store = FakeStore()
        assert await store.load() is None


class TestIBrowserGatewayGetCookies:
    def test_get_cookies_is_abstract(self):
        assert hasattr(IBrowserGateway, "get_cookies")

    @pytest.mark.asyncio
    async def test_concrete_implementation_can_get_cookies(self):
        class FakeBrowser(IBrowserGateway):
            async def start(self): pass
            async def navigate(self, url, wait_seconds=6.0): pass
            async def take_screenshot(self): return b""
            async def click(self, x, y): pass
            async def double_click(self, x, y): pass
            async def triple_click(self, x, y): pass
            async def type_text(self, text): pass
            async def press_key(self, key): pass
            async def scroll(self, x, y, direction, amount): pass
            async def right_click(self, x, y): pass
            async def left_click_drag(self, sx, sy, ex, ey): pass
            async def close(self): pass
            async def get_cookies(self, names):
                return SessionCookies(cf_clearance="cf", ci_session="ci")

        browser = FakeBrowser()
        result = await browser.get_cookies(["cf_clearance", "ci_session"])
        assert result.cf_clearance == "cf"


class TestIProgrammedScriptStore:
    def test_port_is_abstract(self):
        with pytest.raises(TypeError):
            IProgrammedScriptStore()  # type: ignore[abstract]

    def test_load_is_abstract(self):
        assert hasattr(IProgrammedScriptStore, "load")

    @pytest.mark.asyncio
    async def test_concrete_implementation_can_load(self):
        class FakeStore(IProgrammedScriptStore):
            async def load(self):
                return ProgrammedScript(
                    steps=[ProgrammedStep("left_click", {"coordinate": [1, 2]})]
                )

        store = FakeStore()
        script = await store.load()
        assert len(script.steps) == 1

    @pytest.mark.asyncio
    async def test_load_returns_none_when_no_script(self):
        class FakeStore(IProgrammedScriptStore):
            async def load(self): return None

        store = FakeStore()
        assert await store.load() is None
