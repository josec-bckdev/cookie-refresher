"""APScheduler cron jobs — Mon–Fri at 6 AM and 3 PM (America/Bogota)."""
import asyncio
import logging
from typing import Callable, Awaitable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from cookie_refresher.infrastructure.settings import settings

logger = logging.getLogger(__name__)


def build_scheduler(run_refresh: Callable[[], Awaitable[None]]) -> AsyncIOScheduler:
    """
    Returns a configured (but not yet started) AsyncIOScheduler.
    Accepts a coroutine factory so the scheduler itself has no knowledge
    of the use case or its dependencies.
    """
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    async def _job() -> None:
        logger.info("Scheduled refresh triggered")
        try:
            await run_refresh()
        except Exception:
            logger.exception("Scheduled refresh raised an unhandled exception")

    scheduler.add_job(
        _job,
        CronTrigger.from_crontab(settings.schedule_morning, timezone=settings.timezone),
        id="morning_refresh",
        name="Morning cookie refresh (6 AM Mon–Fri)",
        replace_existing=True,
    )
    scheduler.add_job(
        _job,
        CronTrigger.from_crontab(settings.schedule_afternoon, timezone=settings.timezone),
        id="afternoon_refresh",
        name="Afternoon cookie refresh (3 PM Mon–Fri)",
        replace_existing=True,
    )

    logger.info(
        "Scheduler configured — morning: %s, afternoon: %s, tz: %s",
        settings.schedule_morning,
        settings.schedule_afternoon,
        settings.timezone,
    )
    return scheduler
