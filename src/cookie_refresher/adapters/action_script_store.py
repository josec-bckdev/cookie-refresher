"""FileActionScriptStore — persists ActionScript to a JSON file atomically."""
from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cookie_refresher.domain.entities import ActionScript, RecordedStep
from cookie_refresher.domain.ports import IActionScriptStore


class FileActionScriptStore(IActionScriptStore):
    def __init__(self, path: str) -> None:
        self._path = path

    async def save(self, script: ActionScript) -> None:
        path = Path(self._path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "steps": [dataclasses.asdict(s) for s in script.steps],
            "recorded_at": script.recorded_at.isoformat(),
            "use_count": script.use_count,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)

    async def load(self) -> Optional[ActionScript]:
        path = Path(self._path)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        steps = [
            RecordedStep(
                action_type=s["action_type"],
                params=s["params"],
                delay_after_ms=s["delay_after_ms"],
            )
            for s in data["steps"]
        ]
        recorded_at = datetime.fromisoformat(data["recorded_at"])
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        return ActionScript(
            steps=steps,
            recorded_at=recorded_at,
            use_count=data["use_count"],
        )
