"""
Application factory — wires all layers together.

Dependency graph (outer → inner, each only imports from its own layer or inward):
  infrastructure/main.py
    → adapters/controllers/api.py         (FastAPI routes)
    → adapters/gateways/vnc_browser.py    (IBrowserGateway impl)
    → adapters/gateways/vtrack_http.py    (IVtrackGateway impl)
    → infrastructure/anthropic_client.py  (IAgentClient impl)
    → application/use_cases/refresh_session.py (use case)
    → infrastructure/scheduler.py         (cron)
    → infrastructure/settings.py          (config)
"""
import logging
from contextlib import asynccontextmanager

import anthropic
from fastapi import FastAPI

from cookie_refresher.adapters.controllers.api import router, set_use_case_factory
from cookie_refresher.adapters.gateways.vnc_browser import VncBrowserGateway
from cookie_refresher.adapters.gateways.vtrack_http import VtrackHttpGateway
from cookie_refresher.application.use_cases.refresh_session import RefreshSessionUseCase
from cookie_refresher.infrastructure.anthropic_client import AnthropicAgentClient
from cookie_refresher.infrastructure.scheduler import build_scheduler
from cookie_refresher.infrastructure.settings import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


def _build_use_case() -> RefreshSessionUseCase:
    return RefreshSessionUseCase(
        browser=VncBrowserGateway(settings.vnc_browser_url, settings.vnc_container_name),
        vtrack=VtrackHttpGateway(settings.vtrack_api_url),
        agent=AnthropicAgentClient(
            client=anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key),
            model=settings.anthropic_model,
        ),
        login_email=settings.login_email,
        login_password=settings.login_password,
        max_steps=settings.agent_max_steps,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("cookie-refresher starting up")

    set_use_case_factory(_build_use_case)

    async def _run_scheduled_refresh() -> None:
        use_case = _build_use_case()
        result = await use_case.execute()
        if result.success:
            logger.info("Scheduled refresh succeeded in %d steps", result.steps_taken)
        else:
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
