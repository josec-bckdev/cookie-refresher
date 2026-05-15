"""FileProgrammedScriptStore — read-only loader for human-authored programmed_steps.json."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from cookie_refresher.domain.entities import ProgrammedScript, ProgrammedStep
from cookie_refresher.domain.ports import IProgrammedScriptStore


class FileProgrammedScriptStore(IProgrammedScriptStore):
    def __init__(self, path: str) -> None:
        self._path = path

    async def load(self) -> Optional[ProgrammedScript]:
        path = Path(self._path)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        steps = [
            ProgrammedStep(
                action_type=s["action_type"],
                params=s["params"],
                delay_after_ms=s.get("delay_after_ms", 0.0),
            )
            for s in data["steps"]
        ]
        return ProgrammedScript(steps=steps)
