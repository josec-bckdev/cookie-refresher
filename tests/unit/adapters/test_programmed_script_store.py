"""RED: FileProgrammedScriptStore loads programmed_steps.json."""
import json
import pytest
from cookie_refresher.adapters.programmed_script_store import FileProgrammedScriptStore


@pytest.mark.asyncio
class TestFileProgrammedScriptStore:
    async def test_load_returns_none_when_file_absent(self, tmp_path):
        store = FileProgrammedScriptStore(str(tmp_path / "nonexistent.json"))
        assert await store.load() is None

    async def test_load_returns_programmed_script(self, tmp_path):
        path = tmp_path / "programmed_steps.json"
        path.write_text(json.dumps({
            "steps": [
                {"action_type": "left_click", "params": {"coordinate": [100, 200]}, "delay_after_ms": 500.0},
                {"action_type": "get_cookies", "params": {"names": ["cf_clearance", "ci_session"]}},
            ]
        }))
        store = FileProgrammedScriptStore(str(path))
        script = await store.load()
        assert script is not None
        assert len(script.steps) == 2
        assert script.steps[0].action_type == "left_click"
        assert script.steps[0].delay_after_ms == 500.0

    async def test_delay_defaults_to_zero_when_absent(self, tmp_path):
        path = tmp_path / "programmed_steps.json"
        path.write_text(json.dumps({
            "steps": [{"action_type": "get_cookies", "params": {"names": ["cf_clearance"]}}]
        }))
        store = FileProgrammedScriptStore(str(path))
        script = await store.load()
        assert script.steps[0].delay_after_ms == 0.0

    async def test_load_preserves_step_order(self, tmp_path):
        path = tmp_path / "programmed_steps.json"
        steps_data = [
            {"action_type": "left_click", "params": {"coordinate": [1, 2]}},
            {"action_type": "type", "params": {"text": "{{password}}"}},
            {"action_type": "get_cookies", "params": {"names": ["cf_clearance", "ci_session"]}},
        ]
        path.write_text(json.dumps({"steps": steps_data}))
        store = FileProgrammedScriptStore(str(path))
        script = await store.load()
        assert [s.action_type for s in script.steps] == ["left_click", "type", "get_cookies"]

    async def test_store_has_no_save_method(self):
        store = FileProgrammedScriptStore("/data/programmed_steps.json")
        assert not hasattr(store, "save")
