"""RED: IActionScriptStore port contract."""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock
from cookie_refresher.domain.ports import IActionScriptStore
from cookie_refresher.domain.entities import ActionScript, RecordedStep


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
