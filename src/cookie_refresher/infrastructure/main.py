"""
Application factory — wires all layers together.

Dependency graph (outer → inner, each only imports from its own layer or inward):
  infrastructure/main.py
    → adapters/controllers/api.py              (FastAPI routes)
    → adapters/gateways/vnc_browser.py         (IBrowserGateway impl)
    → adapters/gateways/vtrack_http.py         (IVtrackGateway impl)
    → adapters/action_script_store.py          (IActionScriptStore impl)
    → adapters/programmed_script_store.py      (IProgrammedScriptStore impl)
    → infrastructure/anthropic_client.py       (IAgentClient impl)
    → application/use_cases/no_agent_steps.py  (zero-AI programmed use case)
    → application/use_cases/refresh_session.py (full ReAct use case)
    → application/use_cases/replay_session.py  (fast replay use case)
    → infrastructure/scheduler.py              (cron)
    → infrastructure/settings.py               (config)
"""
import logging
from contextlib import asynccontextmanager

import anthropic
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from cookie_refresher.adapters.action_script_store import FileActionScriptStore
from cookie_refresher.adapters.programmed_script_store import FileProgrammedScriptStore
from cookie_refresher.adapters.controllers.api import router, set_job_store, set_use_case_factory
from cookie_refresher.adapters.job_store import InMemoryJobStore
from cookie_refresher.adapters.gateways.vnc_browser import VncBrowserGateway
from cookie_refresher.adapters.gateways.vtrack_http import VtrackHttpGateway
from cookie_refresher.application.use_cases.no_agent_steps import NoAgentStepsUseCase
from cookie_refresher.application.use_cases.refresh_session import RefreshSessionUseCase
from cookie_refresher.application.use_cases.replay_session import ReplaySessionUseCase
from cookie_refresher.infrastructure.anthropic_client import AnthropicAgentClient
from cookie_refresher.infrastructure.scheduler import build_scheduler
from cookie_refresher.infrastructure.settings import settings
from cookie_refresher.infrastructure.telemetry import setup_telemetry

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

_script_store = FileActionScriptStore(settings.action_script_path)
_programmed_store = FileProgrammedScriptStore(settings.programmed_script_path)

_LOGIN_URL = "https://www.rutasljrj.net/rastreo/ljrj/login"


def _make_agent() -> AnthropicAgentClient:
    return AnthropicAgentClient(
        client=anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key),
        model=settings.anthropic_model,
    )


def _make_browser() -> VncBrowserGateway:
    return VncBrowserGateway(
        settings.vnc_browser_url,
        settings.vnc_container_name,
        screenshots_dir=settings.screenshots_dir,
    )


def _make_vtrack() -> VtrackHttpGateway:
    return VtrackHttpGateway(settings.vtrack_api_url)


async def _build_use_case():
    programmed = await _programmed_store.load()
    if programmed:
        logger.info("Programmed mode — %d steps, zero AI calls", len(programmed.steps))
        return NoAgentStepsUseCase(
            browser=_make_browser(),
            vtrack=_make_vtrack(),
            script=programmed,
            login_url=_LOGIN_URL,
            login_email=settings.login_email,
            login_password=settings.login_password,
        )

    script = await _script_store.load()
    if script:
        logger.info(
            "Replay mode — script has %d steps, used %d times",
            len(script.steps),
            script.use_count,
        )
        return ReplaySessionUseCase(
            browser=_make_browser(),
            vtrack=_make_vtrack(),
            agent=_make_agent(),
            script=script,
            login_url=_LOGIN_URL,
            login_email=settings.login_email,
            login_password=settings.login_password,
            randomness_pct=settings.replay_randomness_pct,
            script_store=_script_store,
        )

    logger.info("No script found — running full ReAct loop (will record on success)")
    return RefreshSessionUseCase(
        browser=_make_browser(),
        vtrack=_make_vtrack(),
        agent=_make_agent(),
        login_email=settings.login_email,
        login_password=settings.login_password,
        max_steps=settings.agent_max_steps,
        script_store=_script_store,
        max_inter_step_ms=settings.max_inter_step_ms,
    )


_tracer = trace.get_tracer(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("cookie-refresher starting up")
    setup_telemetry(app, settings.otlp_endpoint)

    set_use_case_factory(_build_use_case)
    set_job_store(InMemoryJobStore())

    async def _run_scheduled_refresh() -> None:
        with _tracer.start_as_current_span("refresh.scheduled") as span:
            use_case = await _build_use_case()
            result = await use_case.execute()
            span.set_attribute("job.mode", result.mode or "unknown")
            span.set_attribute("job.steps_taken", result.steps_taken)
            if result.success:
                logger.info("Scheduled refresh succeeded in %d steps", result.steps_taken)
            else:
                span.set_status(StatusCode.ERROR, result.error or "")
                span.set_attribute("job.failure_reason", result.failure_reason or "unknown")
                logger.error("Scheduled refresh failed: %s", result.error)

    scheduler = build_scheduler(_run_scheduled_refresh)
    scheduler.start()
    logger.info("Scheduler started")

    yield

    scheduler.shutdown(wait=False)
    logger.info("cookie-refresher shut down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="cookie-refresher",
        description="AI agent that refreshes vtrack session cookies via Computer Use",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()
