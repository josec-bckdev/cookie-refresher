# cookie-refresher — Architecture & Design

**Date**: 2026-05-13
**Scope**: Clean Architecture implementation with ReAct agentic pattern and Record & Replay optimisation

> **Related design document**: [RECORD&REPLAY.md](RECORD&REPLAY.md) — full design, implementation details, and timing reference for the action script recording and replay feature.

---

## 1. The Problem

The vtrack system depends on two browser cookies — `cf_clearance` (issued by Cloudflare) and `ci_session` (the application session) — that expire and must be periodically renewed. Because Cloudflare's challenge is visual and browser-level, no simple HTTP client can solve it. The solution is an AI agent that controls a real browser, navigates the login flow, and hands the extracted cookies to the vtrack API.

---

## 2. Layer Hierarchy (Clean Architecture)

The entire codebase is organised around one rule: **dependencies point inward only**.

```
┌─────────────────────────────────────────────────────────────┐
│ INFRASTRUCTURE (frameworks, external services)              │
│  • FastAPI app setup (main.py)                              │
│  • Anthropic SDK integration (anthropic_client.py)          │
│  • APScheduler cron jobs (scheduler.py)                     │
│  • Settings/config (settings.py)                            │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ ADAPTERS (domain ports → concrete I/O)                      │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Gateways (HTTP clients)                              │   │
│  │  • VncBrowserGateway: IBrowserGateway → HTTP/Docker  │   │
│  │  • VtrackHttpGateway: IVtrackGateway → HTTP POST     │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Stores (entity persistence)                          │   │
│  │  • InMemoryJobStore: IJobStore → dict in RAM         │   │
│  │  • FileActionScriptStore: IActionScriptStore → JSON  │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Controllers (HTTP request/response)                  │   │
│  │  • FastAPI router, request handlers, responses       │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ APPLICATION (use cases, orchestration logic)                │
│  • RefreshSessionUseCase: ReAct loop + action recording     │
│  • ReplaySessionUseCase: fast-path script replay            │
│  • ActionDispatcher: action → gateway method routing        │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ DOMAIN (pure business logic, no I/O, no frameworks)        │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Entities                                             │   │
│  │  • Job, JobStatus (async job tracking)               │   │
│  │  • SessionCookies (frozen value object)              │   │
│  │  • ActionRequest, AgentStep (loop data)              │   │
│  │  • AgentResult (outcome)                             │   │
│  │  • RecordedStep (frozen — one captured browser act)  │   │
│  │  • ActionScript (mutable — recorded sequence)        │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Ports (interfaces defining dependencies)             │   │
│  │  • IBrowserGateway (browser control)                 │   │
│  │  • IVtrackGateway (cookie delivery)                  │   │
│  │  • IAgentClient (AI reasoning)                       │   │
│  │  • IJobStore (job lifecycle)                         │   │
│  │  • IActionScriptStore (script persistence)           │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

Each layer imports only from the layer below it (and its own layer). Infrastructure can import from all layers; adapters import from application and domain; application imports from domain only; domain imports nothing external.

---

## 3. Domain Layer — `domain/`

The innermost layer. Zero external imports — only the Python standard library.

### 3.1 Entities (`domain/entities.py`)

All entities are immutable or frozen where appropriate. No framework imports. No I/O.

**`SessionCookies`** — frozen value object. Validates that both cookie strings are non-empty on construction. The only valid output of the entire system.

```python
@dataclass(frozen=True)
class SessionCookies:
    cf_clearance: str
    ci_session: str

    def __post_init__(self) -> None:
        if not self.cf_clearance or not self.cf_clearance.strip():
            raise ValueError("cf_clearance cannot be empty")
        if not self.ci_session or not self.ci_session.strip():
            raise ValueError("ci_session cannot be empty")
```

**`ActionRequest`** — frozen value object. One browser action Claude wants to execute: `action_type` (e.g. `"left_click"`), `params` (e.g. `{"coordinate": [x, y]}`), and the `tool_use_id` that the Anthropic API requires for tool-result pairing.

**`AgentStep`** — frozen value object. One complete reasoning cycle output: zero or more `ActionRequest`s, whether the agent is done, optional `cookies`, and a `reasoning` string. Immutable because it represents a snapshot in time from the AI model.

**`AgentResult`** — mutable result object. The final outcome of one full refresh attempt. Factory methods enforce invariants at construction:

```python
@dataclass
class AgentResult:
    success: bool
    cookies: Optional[SessionCookies]
    error: Optional[str]
    steps_taken: int
    messages: list = field(default_factory=list)

    @classmethod
    def ok(cls, cookies: SessionCookies, steps_taken: int, messages: list | None = None) -> "AgentResult":
        if cookies is None:
            raise ValueError("cookies required for a success result")
        return cls(success=True, cookies=cookies, error=None, steps_taken=steps_taken, messages=messages or [])

    @classmethod
    def fail(cls, error: str, steps_taken: int, messages: list | None = None) -> "AgentResult":
        if not error:
            raise ValueError("error message required for a failure result")
        return cls(success=False, cookies=None, error=error, steps_taken=steps_taken, messages=messages or [])
```

`ok(...)` requires cookies; `fail(...)` requires an error message. Inconsistent states (success without cookies, failure without a message) are impossible to construct.

**`Job`** — mutable tracking entity. Carries a UUID, `JobStatus`, `steps_taken`, `error`, and a `messages` list (the redacted Claude reasoning for the job status endpoint).

**`JobStatus`** — `str` enum: `PROCESSING`, `SUCCESS`, `FAILED`.

### 3.2 Ports (`domain/ports.py`)

Ports are abstract base classes that define the shape of every external dependency. The domain declares what it needs; adapters provide implementations.

**`IBrowserGateway`** — the full computer-use action surface:

```python
class IBrowserGateway(ABC):
    async def start(self) -> None: ...
    async def navigate(self, url: str, wait_seconds: float = 6.0) -> None: ...
    async def take_screenshot(self) -> bytes: ...
    async def click(self, x: int, y: int) -> None: ...
    async def double_click(self, x: int, y: int) -> None: ...
    async def triple_click(self, x: int, y: int) -> None: ...
    async def type_text(self, text: str) -> None: ...
    async def press_key(self, key: str) -> None: ...
    async def scroll(self, x: int, y: int, direction: str, amount: int) -> None: ...
    async def right_click(self, x: int, y: int) -> None: ...
    async def left_click_drag(self, start_x: int, start_y: int, end_x: int, end_y: int) -> None: ...
    async def close(self) -> None: ...
```

The domain never imports a VNC library, Docker SDK, or HTTP client. It just says "I need a thing that can do these operations."

**`IVtrackGateway`** — minimal and focused:

```python
class IVtrackGateway(ABC):
    async def post_cookies(self, cookies: SessionCookies) -> bool: ...
```

**`IAgentClient`** — single method:

```python
class IAgentClient(ABC):
    async def complete(self, messages: list[dict]) -> AgentStep: ...
```

Given a message history (screenshots + prior reasoning), return the next action(s). The domain doesn't know about the Anthropic SDK, token limits, or thinking blocks.

**`IJobStore`** — job lifecycle:

```python
class IJobStore(ABC):
    async def create(self) -> Job: ...
    async def get(self, job_id: str) -> Optional[Job]: ...
    async def update(self, job_id: str, result: AgentResult) -> None: ...
```

The domain owns the entity shape; adapters own the storage mechanism.

---

## 4. Application Layer — `application/use_cases/`

Two use cases: `refresh_session.py` (full ReAct loop) and `replay_session.py` (fast-path replay). Both import only from `domain/`. No SDK, no HTTP library. See [RECORD&REPLAY.md](RECORD&REPLAY.md) for full design of the replay feature.

### 4.1 `ActionDispatcher` — single-responsibility translator

Maps Claude's Computer Use action dictionaries to concrete `IBrowserGateway` calls. The mapping is a flat if-chain keyed on `action_type`:

```python
class ActionDispatcher:
    def __init__(self, browser: IBrowserGateway) -> None:
        self._browser = browser

    async def dispatch(self, action: dict) -> object:
        action_type = action.get("action", "")

        if action_type == "screenshot":
            screenshot_bytes = await self._browser.take_screenshot()
            return [{"type": "image", "source": {"type": "base64", ...}}]

        if action_type == "left_click":
            x, y = action["coordinate"]
            await self._browser.click(x, y)
            return "left_click executed"

        if action_type == "type":
            await self._browser.type_text(action["text"])
            return "type executed"

        # ... double_click, triple_click, key, wait, scroll, right_click, left_click_drag
```

For `screenshot`, it returns a base64-encoded image block ready to be fed back into the message list. For all other actions, it returns a string confirmation. No loop logic lives here.

### 4.2 `RefreshSessionUseCase` — the orchestrator

Receives all three ports and credentials via constructor injection — never instantiates gateways itself (Dependency Inversion). Its public method is `execute() → AgentResult`:

```python
async def execute(self) -> AgentResult:
    await self._browser.start()
    try:
        return await self._run_loop()
    finally:
        await self._browser.close()  # always cleans up, even on exception
```

#### The ReAct Loop

**Pattern**: Observe → Think → Act → Observe → ...

```python
async def _run_loop(self) -> AgentResult:
    await self._browser.navigate(self._login_url)
    messages: list[dict] = []
    step = 0

    while step < self._max_steps:
        # OBSERVE: screenshot appended to message history
        screenshot = await self._browser.take_screenshot()
        messages = self._append_screenshot_observation(messages, screenshot, ...)

        # THINK: send to Claude, get back actions and reasoning
        agent_step = await self._agent.complete(messages)
        if agent_step.reasoning:
            logger.info("Agent thought: %s", agent_step.reasoning)

        messages.append({"role": "assistant", "content": self._build_assistant_content(agent_step)})
        step += 1

        if agent_step.is_done:
            return await self._finalise(agent_step.cookies, step, messages)

        # ACT: execute actions and feed results back as next user turn
        tool_results = await self._execute_actions(agent_step.actions)
        messages.append({"role": "user", "content": tool_results})

    return AgentResult.fail(f"Max steps ({self._max_steps}) exceeded...", steps_taken=step, messages=self._redact(...))
```

**Why ReAct over alternatives?**

- **Plan-and-Execute**: pre-commits to a step sequence — brittle against dynamic Cloudflare challenges whose UI changes every run.
- **Reflection**: adds a self-critique layer. Valuable for code generation; overkill here because the screenshot already provides automatic ground truth.
- **ReAct**: Claude sees the screen, reasons about ONE next action, executes, then sees the updated screen. Handles surprises (captchas, UI drift) without any special-case code. The logged `Thought:` blocks are the full trace.

#### Message History Structure

Messages follow the Claude API alternating-turn format. The use case manages this structure directly:

```python
[
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "Login instructions with credentials..."},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}
        ]
    },
    {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I see the login page, I will click the email field."},
            {"type": "tool_use", "id": "tu_abc", "name": "computer", "input": {"action": "left_click", "coordinate": [x, y]}}
        ]
    },
    {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "tu_abc", "content": "left_click executed"}
        ]
    },
    # Next iteration: new screenshot appended as a new user turn
    {
        "role": "user",
        "content": [
            {"type": "image", "source": {...}}
        ]
    },
    ...
]
```

`_append_screenshot_observation` distinguishes the first turn (full five-step task prompt + screenshot) from subsequent turns (screenshot only). The full prompt covers: login with credentials, DevTools undocking via the ⋮ menu, Network tab filtering for `actualiza_valores`, triple-clicking the cookie header, and the exact `COOKIES_JSON:` output format.

`_build_assistant_content` reconstructs the assistant turn from an `AgentStep`: a text block for reasoning followed by one `tool_use` block per `ActionRequest`.

#### Finalisation

`_finalise` handles the three possible endings:

1. `cookies is None` → fail ("Agent signalled done but provided no cookies")
2. `vtrack.post_cookies()` returns `False` → fail ("Cookies extracted but vtrack rejected")
3. Both succeed → `AgentResult.ok(...)`

#### Redaction

Before attaching messages to any `AgentResult`, credentials are scrubbed:

```python
@staticmethod
def _redact(messages: list, email: str, password: str) -> list:
    redacted = copy.deepcopy(messages)
    for msg in redacted:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "text":
                block["text"] = block["text"].replace(email, "[REDACTED]").replace(password, "[REDACTED]")
            elif block.get("type") == "tool_use":
                inp = block.get("input", {})
                if inp.get("action") == "type" and isinstance(inp.get("text"), str):
                    inp["text"] = inp["text"].replace(email, "[REDACTED]").replace(password, "[REDACTED]")
    return redacted
```

`tool_use` blocks with `action == "type"` also carry credentials (Claude types the email and password via the `computer` tool). Both content types are scrubbed. The use case stores redacted messages in `AgentResult.messages`, which get exposed by the job status endpoint and logs. Observability (can see what Claude did) without credential leakage.

---

## 5. Adapters Layer — `adapters/`

Adapters implement the domain ports using concrete libraries. Each adapter imports from domain and its own external library only.

### 5.1 `VncBrowserGateway` — implements `IBrowserGateway`

The browser is a Docker container (a headful Chromium/VNC sandbox) that exposes a small REST API. `VncBrowserGateway` wraps that API with `httpx`.

**Lifecycle**:

```python
async def start(self) -> None:
    # 1. Locate the Docker container by name
    # 2. Start it if not running
    #    (handles stale container with deleted network: removes it, raises descriptive RuntimeError)
    # 3. Poll /health every 2s up to 60s — waits for {"browser": "running"}
    # 4. Create httpx.AsyncClient

async def close(self) -> None:
    # 1. Close the httpx client
    # 2. Stop the Docker container (timeout=10s, via run_in_executor to avoid blocking the loop)
    # 3. Log errors but never raise (already in a finally block)
```

The container is kept **stopped between refresh cycles** — it only consumes resources while the ReAct loop is running.

**Action translation** — each method POSTs to the VNC service's REST API:

```text
GET  /screenshot          → PNG bytes
POST /navigate            → {"url": str, "wait_seconds": float}
POST /mouse/click         → {"x": int, "y": int}
POST /mouse/double_click  → {"x": int, "y": int}
POST /mouse/triple_click  → {"x": int, "y": int}
POST /mouse/right_click   → {"x": int, "y": int}
POST /mouse/drag          → {"start_x": int, "start_y": int, "end_x": int, "end_y": int}
POST /keyboard/type       → {"text": str}
POST /keyboard/key        → {"key": str}
POST /scroll              → {"x": int, "y": int, "direction": str, "amount": int}
```

The domain never sees HTTP or Docker. It just calls `await browser.click(x, y)`.

### 5.2 `VtrackHttpGateway` — implements `IVtrackGateway`

One-method adapter. POSTs `{"cf_clearance": ..., "ci_session": ...}` to `/session/set-cookies` on the vtrack API. Returns `True` on 2xx, `False` on `HTTPStatusError` or `RequestError` (both are logged, neither is re-raised — the use case handles the boolean).

### 5.3 `InMemoryJobStore` — implements `IJobStore`

A simple `dict[str, Job]`. Generates UUID job IDs. `update` maps `AgentResult.success` → `JobStatus.SUCCESS/FAILED` and copies `steps_taken`, `error`, and `messages` onto the `Job`. In-process only — not shared across replicas. The port boundary means replacing this with a Redis or database adapter requires only creating a new class that satisfies `IJobStore`.

### 5.4 `FileActionScriptStore` — implements `IActionScriptStore`

Persists `ActionScript` as a JSON file. Uses an atomic write pattern: data is written to `<path>.tmp` first, then `os.replace` renames it atomically. Creates parent directories on first save. See [RECORD&REPLAY.md](RECORD&REPLAY.md) for full implementation details.

### 5.5 FastAPI Controller — HTTP surface

Three endpoints:

- `GET /health` — always 200.
- `POST /refresh` — creates a job, schedules `_run_refresh` as a FastAPI `BackgroundTask`, returns **202 Accepted** with `job_id`. The use case factory is called inside the background task (not at startup) so each job gets a fresh `VncBrowserGateway` lifecycle.
- `GET /refresh/{job_id}` — polls `IJobStore` and returns the current job state, including `messages` once complete.

The controller holds module-level references (`_use_case_factory`, `_job_store`) injected by infrastructure at startup via `set_use_case_factory()` and `set_job_store()`. This avoids coupling FastAPI global state to concrete types.

**Why 202 Accepted?** A full refresh cycle takes 20–60 seconds (browser startup, ReAct iterations, API calls). The HTTP request must not block. The client polls `/refresh/{job_id}` until status is `SUCCESS` or `FAILED`.

**Background task error handling**: any unhandled exception is caught and converted to a failed `AgentResult`. The job is always updated, so clients never see a perpetually `PROCESSING` job:

```python
async def _run_refresh(use_case_factory, job_store: IJobStore, job_id: str) -> None:
    use_case = use_case_factory()
    try:
        result: AgentResult = await use_case.execute()
    except Exception as exc:
        logger.exception("Unhandled error in refresh job %s", job_id)
        result = AgentResult.fail(str(exc), steps_taken=0)
    await job_store.update(job_id, result)
```

---

## 6. Infrastructure Layer — `infrastructure/`

The outermost layer. The only layer that may import `anthropic`, the Docker SDK, APScheduler, or pydantic-settings.

### 6.1 `AnthropicAgentClient` — implements `IAgentClient`

The **only** file that touches the Anthropic SDK. All SDK-specific types are translated into domain entities at this boundary.

**API configuration**:

- Model: `claude-opus-4-7`
- Beta: `computer-use-2025-11-24`
- Tool: `computer_20251124` at 1600×1050
- `thinking: {"type": "adaptive"}` — allows extended reasoning through Cloudflare challenges

**Screenshot pruning** — called before every API request:

```python
@staticmethod
def _prune_old_screenshots(messages: list[dict]) -> None:
    """Replace all but the latest screenshot with a text placeholder to cap token cost."""
    _PLACEHOLDER = {"type": "text", "text": "[screenshot omitted]"}
    image_refs: list[tuple] = []

    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for i, block in enumerate(content):
            if block.get("type") == "image":
                image_refs.append((content, i))
            elif block.get("type") == "tool_result":
                inner = block.get("content")
                if isinstance(inner, list):
                    for j, inner_block in enumerate(inner):
                        if inner_block.get("type") == "image":
                            image_refs.append((inner, j))

    for content_list, idx in image_refs[:-1]:
        content_list[idx] = _PLACEHOLDER
```

Walks both top-level image blocks and images nested inside `tool_result` content. Keeps only the last screenshot. After 10+ iterations this is crucial — without pruning, message history grows by ~100 KB per step.

**Response translation**:

```python
async def complete(self, messages: list[dict]) -> AgentStep:
    self._prune_old_screenshots(messages)
    response = await self._beta_messages.create(...)

    actions: list[ActionRequest] = []
    cookies: Optional[SessionCookies] = None
    reasoning_parts: list[str] = []

    for block in response.content:
        if block.type == "thinking":
            reasoning_parts.append(block.thinking or "")
        elif block.type == "text":
            reasoning_parts.append(block.text)
            cookies = self._try_parse_cookies(block.text)  # regex: COOKIES_JSON: {...}
        elif block.type == "tool_use" and block.name == "computer":
            input_data = block.input or {}
            actions.append(ActionRequest(
                action_type=input_data.get("action", "unknown"),
                params={k: v for k, v in input_data.items() if k != "action"},
                tool_use_id=block.id,
            ))

    # Done if cookies extracted OR model finished without requesting any actions
    is_done = bool(cookies) or (response.stop_reason == "end_turn" and not actions)

    return AgentStep(actions=actions, is_done=is_done, cookies=cookies, reasoning=...)
```

`is_done` has a dual condition: the primary signal is the `COOKIES_JSON:` sentinel in a text block (parsed via regex `r"COOKIES_JSON:\s*(\{[^}]+\})"` → `json.loads` → `SessionCookies`); the secondary signal is `end_turn` with no pending tool calls, meaning the model finished cleanly without requesting further actions.

Token usage is logged at INFO on every call (input, output, cache_read, cache_write) for cost monitoring.

### 6.2 `Settings` — `infrastructure/settings.py`

`pydantic-settings` reads from `.env`. All tunables in one place: API key, model, max steps, VNC URL, container name, vtrack URL, login credentials, cron expressions, timezone, log level.

### 6.3 `Scheduler` — `infrastructure/scheduler.py`

`APScheduler` with `AsyncIOScheduler`. Two cron jobs configured from settings: 6 AM and 3 PM Monday–Friday, `America/Bogota` timezone. Accepts a coroutine factory so it has zero knowledge of the use case or its dependencies — if the scheduled call raises, the exception is caught and logged without crashing the scheduler.

### 6.4 Application Factory — `infrastructure/main.py`

`_build_use_case()` is an **async factory** that loads the action script from `FileActionScriptStore` and routes to either `ReplaySessionUseCase` (fast path, when a script exists) or `RefreshSessionUseCase` (full ReAct loop, when no script exists). Called fresh for each job and each scheduled run so every refresh cycle gets its own `VncBrowserGateway` lifecycle with clean state.

The factory is async because `IActionScriptStore.load()` is async. The controller's `_run_refresh` was updated to detect and await async factories via `inspect.iscoroutinefunction`, preserving backward compatibility with synchronous factories.

The `lifespan` context manager (FastAPI startup/shutdown):

1. Injects factory and job store into the controller via `set_use_case_factory` / `set_job_store`.
2. Defines `_run_scheduled_refresh` (awaits the factory, logs outcome).
3. Passes that callback to `build_scheduler`, starts it.
4. On shutdown: `scheduler.shutdown(wait=False)`.

See [RECORD&REPLAY.md](RECORD&REPLAY.md) for the full routing logic.

---

## 7. Design Process Order (from git history)

The commit log shows the TDD discipline applied strictly across every feature addition:

```text
35e1384  Initial working implementation

a3fd6ef  test(domain): add failing tests for Job entity and JobStatus     [RED]
01caa5a  feat(domain): add Job entity, JobStatus enum, and IJobStore port [GREEN]

43d9208  test(adapters): add failing tests for InMemoryJobStore            [RED]
5521659  feat(adapters): add InMemoryJobStore implementation              [GREEN]

2b6cd66  test(adapters): update controller tests for async job pattern    [RED]
05d28dc  feat(adapters): refactor /refresh to 202 async job pattern       [GREEN]

c2cd4c2  feat(infra): wire InMemoryJobStore at startup

4d5d610  test(adapters): add failing tests for screenshot pruning         [RED]
28c1fdf  feat(adapters): prune old screenshots before API call            [GREEN]

71d99e4  test(domain,adapters,application): add failing tests for messages in job result [RED]
459d087  feat(domain,adapters,application): expose redacted messages in job status       [GREEN]
```

Every feature was added by writing the test first (committing the red state), then writing the minimum implementation (committing the green state). The async job pattern, screenshot pruning, and message redaction each followed this cycle explicitly.

---

## 8. Key Design Decisions

| Decision | Why |
| --- | --- |
| ReAct over Plan-and-Execute | Cloudflare UI changes every run; ReAct handles surprises without special-case code |
| Ports defined in `domain/` | Ports declare what the domain needs — they belong to the domain, not to application |
| Use case factory, not singleton | Each refresh cycle needs a fresh `VncBrowserGateway` with its own Docker + httpx lifecycle |
| `_prune_old_screenshots` in `AnthropicAgentClient` | Token cost: only the latest screenshot matters for the next decision; older ones are dead weight |
| `_redact` in the use case | Credentials are in the first message turn; redacting before attaching to `AgentResult` prevents leakage through the status API |
| `IBrowserGateway.start/close` explicit lifecycle | The Docker container is expensive — stopped after each run, not left idle |
| `is_done` dual condition | Model can finish by outputting `COOKIES_JSON:` or by `end_turn` with no pending tool calls |
| `messages` as `list[dict]` | The Anthropic API format evolves; typing it as domain objects would require multiple Pydantic models. Only `AnthropicAgentClient` understands the structure |
| 202 Accepted + polling | A refresh takes 20–60 seconds; blocking HTTP is unacceptable. Polling is simpler than WebSockets for this use case |
| `InMemoryJobStore` for now | During a single cycle only one job exists at a time; the port boundary means swapping to Redis requires only a new class |
| Record & Replay optimisation | Login sequence is deterministic; replaying recorded actions reduces Claude API calls from ~30 to 1 (~128s → ~25s) |
| Credential masking in `RecordedStep` | `{{email}}` / `{{password}}` sentinels ensure raw credentials are never written to the JSON script file |
| Atomic JSON write via `os.replace` | The script file must never be partially written; a power-cut mid-save must not corrupt the recording |

---

## 9. System Hierarchy Summary

### 9.1 Request Flow

```
HTTP POST /refresh
  → controller.trigger_refresh()
    → job_store.create()
    → background_tasks.add_task(_run_refresh)
    → return 202 Accepted {job_id}

[Background task]
  → use_case_factory() builds RefreshSessionUseCase
    → RefreshSessionUseCase.execute()
      → browser.start()
        → Docker SDK: start container
        → poll /health until browser ready
      → _run_loop()
        ┌─ per iteration ──────────────────────────┐
        │ screenshot = browser.take_screenshot()   │
        │ messages = append_screenshot(messages)   │
        │ agent_step = agent.complete(messages)    │
        │   AnthropicAgentClient.complete()        │
        │     _prune_old_screenshots(messages)     │
        │     → anthropic.beta.messages.create()  │
        │     → parse blocks → ActionRequest[]    │
        │ messages.append(assistant turn)          │
        │ if agent_step.is_done:                   │
        │   → _finalise(cookies, step, messages)  │
        │     → vtrack.post_cookies(cookies)      │
        │       → HTTP POST /session/set-cookies  │
        │     → return AgentResult.ok(...)        │
        │ for action in agent_step.actions:        │
        │   result = dispatcher.dispatch(action)   │
        │     → browser.click/type/scroll/...     │
        │       → HTTP POST to VNC service        │
        │ messages.append(tool_results user turn)  │
        └──────────────────────────────────────────┘
      → browser.close()
        → httpx client closed
        → Docker container stopped
      → job_store.update(job_id, result)

HTTP GET /refresh/{job_id}
  → job_store.get(job_id)
  → return {status, steps_taken, error, messages}
```

### 9.2 Data Flow

```
Domain Entities:
  SessionCookies (source of truth)
    ↑ extracted by AnthropicAgentClient._try_parse_cookies(text)
    ← from Claude's COOKIES_JSON: {"cf_clearance": "...", "ci_session": "..."}

  ActionRequest[] (per loop iteration)
    ↑ built by AnthropicAgentClient from tool_use blocks
    → passed to ActionDispatcher.dispatch()
    → mapped to IBrowserGateway methods

  AgentResult (outcome)
    ↑ built by RefreshSessionUseCase._finalise()
    ← contains success flag, cookies, error, steps, redacted messages
    → stored by job_store.update()
    → exposed as RefreshJobResponse to client

  Job (tracking)
    ↑ created by job_store.create() on POST /refresh
    ← status: PROCESSING → SUCCESS | FAILED
    → polled via GET /refresh/{job_id}
```

### 9.3 Dependency Graph

```
infrastructure/main.py
  ├─ adapters/controllers/api.py
  │   ├─ domain/entities.py (Job, AgentResult)
  │   └─ domain/ports.py (IJobStore)
  │
  ├─ adapters/gateways/vnc_browser.py
  │   ├─ domain/ports.py (IBrowserGateway)
  │   ├─ httpx
  │   └─ docker
  │
  ├─ adapters/gateways/vtrack_http.py
  │   ├─ domain/entities.py (SessionCookies)
  │   ├─ domain/ports.py (IVtrackGateway)
  │   └─ httpx
  │
  ├─ adapters/action_script_store.py
  │   ├─ domain/entities.py (ActionScript, RecordedStep)
  │   ├─ domain/ports.py (IActionScriptStore)
  │   └─ json, os, pathlib  (stdlib only)
  │
  ├─ infrastructure/anthropic_client.py       ← only file that imports anthropic
  │   ├─ domain/entities.py (ActionRequest, AgentStep, SessionCookies)
  │   ├─ domain/ports.py (IAgentClient)
  │   └─ anthropic
  │
  ├─ application/use_cases/refresh_session.py
  │   ├─ domain/entities.py (ActionScript, AgentResult, RecordedStep, SessionCookies)
  │   └─ domain/ports.py (IBrowserGateway, IVtrackGateway, IAgentClient, IActionScriptStore)
  │   (no adapter or infrastructure imports)
  │
  ├─ application/use_cases/replay_session.py
  │   ├─ domain/entities.py (ActionScript, AgentResult, RecordedStep, SessionCookies)
  │   ├─ domain/ports.py (IBrowserGateway, IVtrackGateway, IAgentClient, IActionScriptStore)
  │   └─ application/use_cases/refresh_session.py (ActionDispatcher only)
  │
  ├─ infrastructure/scheduler.py
  │   ├─ apscheduler
  │   └─ infrastructure/settings.py
  │
  └─ infrastructure/settings.py
      └─ pydantic-settings

domain/
  ├─ entities.py  (no imports beyond stdlib)
  └─ ports.py     (imports domain/entities only)

Constraint summary:
  domain      → stdlib only
  application → domain only (replay_session imports ActionDispatcher from refresh_session — same layer)
  adapters    → domain, stdlib, external libs (httpx, docker, fastapi)
  infra       → all layers, stdlib, external libs (anthropic, apscheduler, pydantic-settings)
```

---

## 10. TDD Workflow & Atomic Commits

The project enforces **red → green → refactor** strictly:

1. **RED** — write a failing test first, commit: `test(scope): add failing test for X`
2. **GREEN** — write minimum code to pass, commit: `feat(scope): implement X`
3. **REFACTOR** — clean up without changing behaviour (only if needed): `refactor(scope): simplify X`

Each commit is atomic and passes all tests. Test commits are never squashed into implementation commits — the test commit is proof of the red state.

**Valid scopes**: `agent`, `domain`, `adapters`, `infra`, `scheduler`, `login`, `cookies`, `config`

---

## 11. Design Patterns Reference

| Pattern | Where | Why |
|---------|-------|-----|
| **Clean Architecture** | All layers | Testability, maintainability, framework independence |
| **Ports & Adapters** | `domain/ports.py` + `adapters/` | Dependency inversion; easy mocking at boundaries |
| **ReAct Loop** | `application/use_cases/refresh_session.py` | Handles dynamic, surprise-prone tasks (Cloudflare) |
| **Async Job Pattern** | `adapters/controllers/api.py` | Non-blocking HTTP; rich status polling |
| **Factory Method** | `infrastructure/main.py` | Fresh dependency graph per cycle |
| **Lifespan Context Manager** | `infrastructure/main.py` | Clean startup/shutdown, scheduler lifecycle |
| **Value Objects** | `domain/entities.py` (`SessionCookies`, `ActionRequest`) | Immutability, validation, semantic grouping |
| **Factory Methods on Result** | `domain/entities.py` (`AgentResult.ok`, `.fail`) | Enforce invariants; readable construction |
| **Screenshot Pruning** | `infrastructure/anthropic_client.py` | Token efficiency; latency reduction |
| **Redaction** | `application/use_cases/refresh_session.py` | Security (no creds in logs or `tool_use` blocks) + observability (redacted reasoning) |
| **Record & Replay** | `application/use_cases/replay_session.py` + `adapters/action_script_store.py` | 30× reduction in Claude API calls; ~5× total latency improvement |
| **Credential Masking** | `application/use_cases/refresh_session.py` (`_mask_credentials`) | Sentinel substitution at record time; raw credentials never reach the JSON file |
| **Atomic Commits + TDD** | All commits | Evidence of correctness; reviewability; design clarity |

---

## 12. Testability by Design

Every layer is independently testable because all external dependencies are ports.

**Domain** — pure Python, no mocking needed:

```python
def test_session_cookies_requires_non_empty_fields():
    with pytest.raises(ValueError):
        SessionCookies(cf_clearance="", ci_session="value")

def test_agent_result_ok_requires_cookies():
    with pytest.raises(ValueError):
        AgentResult.ok(cookies=None, steps_taken=5)
```

**Application** — mock all three ports, test loop logic:

```python
@pytest.mark.asyncio
async def test_refresh_loop_posts_cookies_on_success():
    browser = AsyncMock(spec=IBrowserGateway)
    agent = AsyncMock(spec=IAgentClient)
    vtrack = AsyncMock(spec=IVtrackGateway)

    # Agent returns done=True with cookies on first call
    agent.complete.return_value = AgentStep(
        actions=[], is_done=True,
        cookies=SessionCookies(cf_clearance="abc", ci_session="xyz"),
        reasoning="done"
    )
    vtrack.post_cookies.return_value = True

    use_case = RefreshSessionUseCase(browser, vtrack, agent, "email", "pw")
    result = await use_case.execute()

    assert result.success
    vtrack.post_cookies.assert_called_once()
```

**Adapters** — mock httpx / Docker, test translation and error paths without real infrastructure.

No adapter needs a real Anthropic API key, real Docker daemon, or real VNC service.

---

## 13. Next Steps

The architecture is designed to accommodate these extensions without touching the domain or use case:

- Migrate `InMemoryJobStore` to a database adapter (PostgreSQL, Redis) by implementing `IJobStore`
- Add request signing and API key validation at the controller boundary
- Instrument with OpenTelemetry (traces on the ReAct loop and replay path, metrics on step count and latency)
- Add retry logic and backoff inside `VncBrowserGateway.start()` for transient container failures
- Implement a persistent job history endpoint for auditing past refresh cycles
- Expose a `DELETE /script` admin endpoint to force a full re-record without container access
- Add script invalidation logic (e.g. invalidate after N failures or if the site layout changes)
