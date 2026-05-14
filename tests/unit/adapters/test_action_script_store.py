"""RED: FileActionScriptStore — save and load behaviour."""
import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from cookie_refresher.domain.entities import ActionScript, RecordedStep
from cookie_refresher.adapters.action_script_store import FileActionScriptStore


STEP = RecordedStep("left_click", {"coordinate": [100, 200]}, 520.0)
SCRIPT = ActionScript(
    steps=[STEP],
    recorded_at=datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc),
    use_count=3,
)


@pytest.fixture
def store(tmp_path: Path) -> FileActionScriptStore:
    return FileActionScriptStore(str(tmp_path / "script.json"))


@pytest.mark.asyncio
class TestFileActionScriptStore:
    async def test_load_returns_none_when_file_missing(self, store):
        assert await store.load() is None

    async def test_save_creates_file(self, store, tmp_path):
        await store.save(SCRIPT)
        assert (tmp_path / "script.json").exists()

    async def test_save_and_load_roundtrip(self, store):
        await store.save(SCRIPT)
        loaded = await store.load()

        assert loaded is not None
        assert loaded.use_count == 3
        assert len(loaded.steps) == 1
        assert loaded.steps[0].action_type == "left_click"
        assert loaded.steps[0].params == {"coordinate": [100, 200]}
        assert loaded.steps[0].delay_after_ms == 520.0

    async def test_recorded_at_preserved(self, store):
        await store.save(SCRIPT)
        loaded = await store.load()
        assert loaded.recorded_at == SCRIPT.recorded_at

    async def test_credentials_sentinels_stored_verbatim(self, store):
        script = ActionScript(
            steps=[
                RecordedStep("type", {"text": "{{email}}"}, 30.0),
                RecordedStep("type", {"text": "{{password}}"}, 30.0),
            ],
            recorded_at=datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc),
        )
        await store.save(script)

        raw = json.loads(Path(store._path).read_text())
        texts = [s["params"]["text"] for s in raw["steps"]]
        assert "{{email}}" in texts
        assert "{{password}}" in texts

    async def test_raw_credentials_never_written_to_disk(self, store):
        script = ActionScript(
            steps=[RecordedStep("type", {"text": "{{email}}"}, 30.0)],
            recorded_at=datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc),
        )
        await store.save(script)

        raw_text = Path(store._path).read_text()
        assert "user@x.com" not in raw_text
        assert "secret" not in raw_text

    async def test_save_is_atomic(self, store, tmp_path):
        """Write goes to .tmp first then os.replace — no partial file visible."""
        await store.save(SCRIPT)
        tmp_file = tmp_path / "script.json.tmp"
        assert not tmp_file.exists()

    async def test_overwrite_updates_use_count(self, store):
        await store.save(SCRIPT)
        updated = ActionScript(
            steps=SCRIPT.steps,
            recorded_at=SCRIPT.recorded_at,
            use_count=10,
        )
        await store.save(updated)
        loaded = await store.load()
        assert loaded.use_count == 10

    async def test_creates_parent_dirs_if_needed(self, tmp_path):
        deep_path = str(tmp_path / "a" / "b" / "c" / "script.json")
        store = FileActionScriptStore(deep_path)
        await store.save(SCRIPT)
        assert Path(deep_path).exists()

    async def test_multiple_steps_roundtrip(self, store):
        script = ActionScript(
            steps=[
                RecordedStep("left_click", {"coordinate": [100, 200]}, 500.0),
                RecordedStep("type", {"text": "{{email}}"}, 300.0),
                RecordedStep("key", {"text": "Return"}, 100.0),
            ],
            recorded_at=datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc),
        )
        await store.save(script)
        loaded = await store.load()

        assert len(loaded.steps) == 3
        assert loaded.steps[0].action_type == "left_click"
        assert loaded.steps[1].action_type == "type"
        assert loaded.steps[2].action_type == "key"
