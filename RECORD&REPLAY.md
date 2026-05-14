# Record & Replay — Feature Design

**Date**: 2026-05-13
**Status**: Implemented
**Motivation**: The ReAct loop calls the Claude API on every step (~5–15s per call, ~20–40 calls per run). Since the login action sequence is now known and stable, we can record it once from a successful run and replay it on subsequent runs — making only **one** Claude API call at the end to extract cookies from the final screenshot.

**Result**: ~128s full ReAct run → ~25s replay run; ~30 Claude API calls → 1.

---

## 1. The Problem

A typical successful login run produces 32 ReAct iterations. Each iteration pays:

| Phase | Duration | Notes |
| --- | --- | --- |
| Screenshot capture | ~80ms | I/O bound, fast |
| Claude API (think + respond) | 2.5–3.5s | Dominant cost |
| VNC action dispatch | ~520ms | Per action |

With 32 steps, the Claude API alone accounts for ~100s of the ~128s total. But the login sequence is **deterministic**: same clicks, same typed credentials, same DevTools navigation — every run. The AI reasoning adds nothing beyond the first recorded run.

---

## 2. Design: Two Modes

```
On first run (no script exists):
  RefreshSessionUseCase (full ReAct loop)
    → records every dispatched action + wall-clock timing
    → masks credentials with {{email}} / {{password}} sentinels
    → saves ActionScript to disk on success

On all subsequent runs (script exists):
  ReplaySessionUseCase (fast path)
    → dispatches recorded actions with jitter delays
    → makes ONE Claude API call at the end for cookie extraction
    → increments use_count in the script file
```

Mode selection happens in `infrastructure/main.py`'s use-case factory at runtime, transparent to the HTTP controller.

---

## 3. Domain Layer Changes — `domain/entities.py`

### `RecordedStep` (new — frozen value object)

```python
@dataclass(frozen=True)
class RecordedStep:
    action_type: str      # "left_click", "type", "key", "scroll", etc.
    params: dict          # {"coordinate": [x, y]} | {"text": "{{email}}"} | ...
    delay_after_ms: float # wall-clock dispatch time + capped inter-step gap
```

Credentials in `type` actions are **masked at record time** as `{{email}}` and `{{password}}`. The raw values are never written to the script file.

Follows the same pattern as `ActionRequest`: frozen, dict params, no framework imports.

### `ActionScript` (new — mutable tracking entity)

```python
@dataclass
class ActionScript:
    steps: list[RecordedStep]
    recorded_at: datetime    # ISO-8601 string in JSON
    use_count: int = 0       # incremented on each successful replay
```

Mutable because `use_count` is updated in place after each successful replay and re-saved.

### Why are these in `domain/`?

`RecordedStep` and `ActionScript` are pure business concepts — they represent a recorded login sequence, carry no I/O, and impose no framework coupling. Placing them in `domain/` keeps the boundary clean: adapters and use cases can both import them without violating the dependency rule.

---

## 4. New Port — `domain/ports.py`

### `IActionScriptStore`

```python
class IActionScriptStore(ABC):
    async def save(self, script: ActionScript) -> None: ...
    async def load(self) -> Optional[ActionScript]: ...
```

Two methods, deliberately minimal. `save` is idempotent (overwrites). `load` returns `None` when no script exists. The domain declares what it needs; `FileActionScriptStore` in the adapters layer provides the implementation.

---

## 5. Application Layer Changes

### 5.1 Security Fix — `RefreshSessionUseCase._redact`

Bundled finding: the original `_redact` only scrubbed `type == "text"` content blocks. Claude's `type` computer-use action carries the raw credential in `input.text` of a `tool_use` block, which was being stored unredacted in `AgentResult.messages` and exposed via the job status API.

**Fix** — extended `_redact` to also handle `tool_use` blocks:

```python
for block in content:
    if block.get("type") == "text":
        block["text"] = block["text"].replace(email, "[REDACTED]").replace(password, "[REDACTED]")
    elif block.get("type") == "tool_use":
        inp = block.get("input", {})
        if inp.get("action") == "type" and isinstance(inp.get("text"), str):
            inp["text"] = inp["text"].replace(email, "[REDACTED]").replace(password, "[REDACTED]")
```

This was the first commit in the TDD sequence.

### 5.2 `RefreshSessionUseCase` Extensions

Two new optional constructor params (no-op when absent — existing behaviour unchanged):

```python
def __init__(
    self,
    ...                                          # existing params
    script_store: Optional[IActionScriptStore] = None,
    max_inter_step_ms: float = 3000.0,
) -> None:
```

#### `_execute_and_record` (replaces `_execute_actions`)

Wraps dispatch with wall-clock timing and builds `RecordedStep`s. Screenshot actions are excluded — they are dead overhead in replay mode:

```python
async def _execute_and_record(
    self, actions: list[ActionRequest]
) -> tuple[list[dict], list[RecordedStep]]:
    tool_results, recorded = [], []
    for action_req in actions:
        t0 = time.monotonic()
        result = await self._dispatcher.dispatch(
            {**action_req.params, "action": action_req.action_type}
        )
        t1 = time.monotonic()
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": action_req.tool_use_id,
            "content": result if isinstance(result, list) else str(result),
        })
        if action_req.action_type != "screenshot":
            params = self._mask_credentials(action_req.params, action_req.action_type)
            recorded.append(RecordedStep(action_req.action_type, params, (t1 - t0) * 1000))
    return tool_results, recorded
```

#### `_mask_credentials`

```python
def _mask_credentials(self, params: dict, action_type: str) -> dict:
    if action_type == "type":
        text = params.get("text", "")
        if text == self._login_email:
            return {**params, "text": "{{email}}"}
        if text == self._login_password:
            return {**params, "text": "{{password}}"}
    return params
```

#### Inter-step gap cap in `_run_loop`

The delay between a VNC action completing and the next action starting is mostly Claude API think time (~2.5–3.5s). Replaying that raw gap would make replays needlessly slow. The cap is applied to the last recorded step before dispatching the next batch:

```python
if t_last_step_end is not None and all_recorded:
    inter_ms = min(
        (t_actions_start - t_last_step_end) * 1000, self._max_inter_step_ms
    )
    prev = all_recorded[-1]
    all_recorded[-1] = RecordedStep(
        prev.action_type, prev.params, prev.delay_after_ms + inter_ms
    )
```

`max_inter_step_ms = 3000` caps each inter-step gap. With real timings of 2.5–3.5s the cap fires correctly, producing natural-looking delays without inflated values.

#### Script save on success

```python
if agent_step.is_done:
    result = await self._finalise(agent_step.cookies, step, messages)
    if result.success and self._script_store:
        script = ActionScript(steps=all_recorded, recorded_at=datetime.now(timezone.utc))
        await self._script_store.save(script)
        logger.info("Action script recorded: %d steps", len(all_recorded))
    return result
```

Script is saved **only on success**. A failed run is not recorded — it may have taken a wrong path.

### 5.3 `ReplaySessionUseCase` (new — `application/use_cases/replay_session.py`)

Fast-path use case. One full run = dispatch N recorded steps + one Claude API call.

```python
async def _run_replay(self) -> AgentResult:
    await self._browser.navigate(self._login_url)

    for step in self._script.steps:
        params = self._resolve_credentials(step.params, step.action_type)
        await self._dispatcher.dispatch({**params, "action": step.action_type})
        delay_s = self._jitter(step.delay_after_ms) / 1000
        await asyncio.sleep(delay_s)

    screenshot = await self._browser.take_screenshot()
    messages = self._build_extract_message(screenshot)
    agent_step = await self._agent.complete(messages)
    result = await self._finalise(agent_step.cookies, steps=len(self._script.steps) + 1)

    if result.success and self._script_store:
        self._script.use_count += 1
        await self._script_store.save(self._script)

    return result
```

`steps_taken` = recorded steps + 1 (the final Claude call) — comparable to the full loop's step count.

#### `_resolve_credentials`

Inverse of `_mask_credentials` — substitutes sentinels with real values at dispatch time:

```python
def _resolve_credentials(self, params: dict, action_type: str) -> dict:
    if action_type == "type":
        text = params.get("text", "")
        if text == "{{email}}":
            return {**params, "text": self._login_email}
        if text == "{{password}}":
            return {**params, "text": self._login_password}
    return params
```

#### `_jitter`

Adds ±`randomness_pct` variation to each delay, producing human-like pacing:

```python
def _jitter(self, ms: float) -> float:
    factor = 1.0 + random.uniform(-self._randomness_pct, self._randomness_pct)
    return max(0.0, ms * factor)
```

Default `randomness_pct = 0.20` (±20%).

#### Extract-only prompt

The single Claude API call uses a minimal prompt — it does not need the full five-step login instructions. It only needs to read the cookie header that is already visible in the DevTools Network panel:

```python
@staticmethod
def _build_extract_message(screenshot: bytes) -> list[dict]:
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": (
                "The browser login flow is complete and DevTools is open on the Network tab.\n"
                "Find the 'cookie:' request header in the visible request headers panel.\n"
                "Triple-click its value to select it, then read both cookie values and output:\n"
                'COOKIES_JSON: {"cf_clearance": "<value>", "ci_session": "<value>"}'
            )},
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.b64encode(screenshot).decode(),
            }},
        ],
    }]
```

`ActionDispatcher` is imported from `refresh_session.py` and reused as-is — no duplication.

---

## 6. Adapter — `adapters/action_script_store.py`

### `FileActionScriptStore` — implements `IActionScriptStore`

```python
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
        os.replace(tmp, path)   # atomic on POSIX

    async def load(self) -> Optional[ActionScript]:
        path = Path(self._path)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        steps = [RecordedStep(**s) for s in data["steps"]]
        recorded_at = datetime.fromisoformat(data["recorded_at"])
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        return ActionScript(steps=steps, recorded_at=recorded_at, use_count=data["use_count"])
```

**Atomic write**: the JSON is written to `<path>.tmp` first, then `os.replace` moves it to the final path. On POSIX systems `os.replace` is an atomic rename — the JSON file is never in a partially-written state.

**Parent dir creation**: `mkdir(parents=True, exist_ok=True)` so the path `/data/action_script.json` works even if `/data/` does not yet exist.

**Timezone safety**: `datetime.fromisoformat` on Python 3.10 returns a naive datetime if the stored string has no offset. The `replace(tzinfo=timezone.utc)` call ensures loaded scripts always carry timezone info, consistent with how they were saved.

**`RecordedStep.params` round-trips cleanly** through JSON because all param values are strings, ints, or lists of ints — the same primitives the Anthropic Computer Use API uses.

---

## 7. Infrastructure Changes

### `settings.py` — three new fields

```python
action_script_path: str = "/data/action_script.json"
replay_randomness_pct: float = 0.20
max_inter_step_ms: float = 3000.0
```

### `main.py` — routing between use cases

`FileActionScriptStore` is created once at module load (not in lifespan) — it is stateless, carries only a path, and can be shared across runs safely.

`_build_use_case` is now an async factory (awaiting `load`) and registered with the controller via the same `set_use_case_factory` mechanism. The controller's `_run_refresh` was updated to `await` the factory if it is a coroutine:

```python
async def _run_refresh(use_case_factory, job_store: IJobStore, job_id: str) -> None:
    if inspect.iscoroutinefunction(use_case_factory):
        use_case = await use_case_factory()
    else:
        use_case = use_case_factory()
    ...
```

This preserves backward compatibility with synchronous factories.

The factory logic:

```python
async def _build_use_case():
    script = await _script_store.load()
    if script:
        logger.info("Replay mode selected — script has %d steps, used %d times", ...)
        return ReplaySessionUseCase(...)
    logger.info("No action script found — running full ReAct loop (will record on success)")
    return RefreshSessionUseCase(..., script_store=_script_store, max_inter_step_ms=...)
```

**Fallback behaviour**: if a replay run fails, the script is not deleted. The next invocation retries replay. To force a full re-record, delete the script file (`/data/action_script.json`).

---

## 8. Recorded Action Sequence (from a real 32-step run)

This is the exact sequence the recording captures. Coordinates are screen pixels at 1600×1050.

| Step | action\_type | params | Purpose |
| --- | --- | --- | --- |
| 1 | `left_click` | `{"coordinate": [642, 470]}` | Open Perfil dropdown |
| 2 | `left_click` | `{"coordinate": [538, 535]}` | Select Responsable role |
| 3 | `left_click` | `{"coordinate": [642, 371]}` | Click email field |
| 4–N | `left_click` | `{"coordinate": [642, 420]}` | Cloudflare checkbox (retried until cleared) |
| N+1 | `type` | `{"text": "{{email}}"}` | Type email credential (masked) |
| N+2 | `type` | `{"text": "{{password}}"}` | Type password credential (masked) |
| N+3 | `left_click` | `{"coordinate": [759, 519]}` | Click Ingresar (login button) |
| N+4 | `key` | `{"text": "F12"}` | Open DevTools |
| N+5 | `left_click` | `{"coordinate": [1017, 357]}` | Focus DevTools panel |
| N+6 | `left_click` | `{"coordinate": [1245, 624]}` | Open ⋮ undock menu |
| N+7 | `left_click` | `{"coordinate": [1180, 658]}` | Select "Undock into separate window" |
| N+8 | `left_click` | `{"coordinate": [170, 322]}` | Click Network tab |
| N+9 | `scroll` | `{"coordinate": [740, 800], "scroll_direction": "down", "scroll_amount": 5}` | Scroll to cookie header |
| final | ONE Claude call | screenshot → COOKIES\_JSON | Extract cookies |

`screenshot` actions (which Claude requested to verify its own progress) are **excluded** from the recording — they serve no purpose during replay.

---

## 9. TDD Commit Sequence

Strictly red → green per commit, layer by layer:

```text
fix(application): extend _redact to scrub tool_use type-action input params     [f8b4efe]

test(domain): add failing tests for RecordedStep and ActionScript entities       [38da476]
feat(domain): implement RecordedStep and ActionScript                            [905ed22]

test(domain): add failing tests for IActionScriptStore port                     [da350a2]
feat(domain): add IActionScriptStore port                                       [806ce3a]

test(application): add failing tests for ReplaySessionUseCase                  [21639ed]
feat(application): implement ReplaySessionUseCase                               [d3f1a45]

test(application): add failing tests for action recording in RefreshSessionUseCase  [ebcac53]
feat(application): record and mask credentials in action script on successful run   [e783a30]

test(adapters): add failing tests for FileActionScriptStore save and load       [6398883]
feat(adapters): implement FileActionScriptStore with atomic write               [6885d16]

feat(infra): wire FileActionScriptStore and use-case routing in main.py         [9e3908c]
```

Each test commit was verified to fail (ImportError or assertion error) before the implementation commit was written.

---

## 10. Key Design Decisions

| Decision | Rationale |
| --- | --- |
| Credential masking at record time (not at save time) | Prevents raw values from ever appearing in memory during serialisation |
| Inter-step gap cap (`max_inter_step_ms = 3000`) | Claude API think time is ~2.5–3.5s; without a cap, replays would inherit that wait and eliminate most of the speedup |
| `±20%` jitter on delays | Human-like pacing; reduces risk of timing-based bot detection |
| `screenshot` actions excluded from recording | Screenshot dispatches take ~80ms and serve only Claude's reasoning loop — pure overhead in replay |
| Atomic write via `os.replace` | The JSON file must never be partially written; a power-cut mid-save should not corrupt the script |
| Single Claude API call for extraction | The login sequence is mechanical; only the cookie-reading step requires vision |
| `use_count` tracked in the script | Provides operational visibility: how many successful replays have run since the last re-record |
| Fallback: replay failure retries replay (not ReAct) | A one-time network glitch should not trigger a full re-record; delete the file to force it |
| `IActionScriptStore` in `domain/ports.py` | The use cases need to save and load scripts — the port belongs to the domain that declares the need |
| `FileActionScriptStore` in `adapters/` | JSON + filesystem is an I/O concern; swapping to a database adapter requires only a new class |

---

## 11. Timing Reference

| Phase | Full ReAct | Replay |
| --- | --- | --- |
| Browser start + navigate | ~7s | ~7s |
| Claude API calls | ~100s (32 × 3s) | ~3s (1 call) |
| VNC action dispatch | ~17s (32 × 0.52s) | ~17s (same actions) |
| **Total** | **~128s** | **~25s** |

The ~17s of VNC dispatch time is identical in both modes — the improvement is purely from eliminating 31 Claude API calls.

---

## 12. Verification Checklist

1. `pytest` — 111 tests pass, coverage ≥ 80%.
2. `POST /refresh` with no script file → logs: `"No action script found — running full ReAct loop"`.
3. On success → logs: `"Action script recorded: N steps"`.
4. Inspect `/data/action_script.json` — `type` action params show `{{email}}`/`{{password}}`, not raw credentials.
5. Second `POST /refresh` → logs: `"Replay mode selected — script has N steps"`.
6. Exactly ONE `"Agent API usage"` log line per run (vs ~30+ in ReAct mode).
7. `GET /refresh/{job_id}` → status `SUCCESS`, cookies posted to vtrack.
8. `messages` in job response contain no raw email or password strings.
9. Delete `/data/action_script.json` → next run logs `"No action script found"` and re-records.
