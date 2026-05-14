"""FastAPI controller — health check + manual trigger + status endpoints."""
import inspect
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from cookie_refresher.domain.entities import AgentResult
from cookie_refresher.domain.ports import IJobStore

logger = logging.getLogger(__name__)

router = APIRouter()

# Injected at startup by infrastructure/main.py
_use_case_factory = None
_job_store: Optional[IJobStore] = None


def set_use_case_factory(factory) -> None:
    global _use_case_factory
    _use_case_factory = factory


def set_job_store(store: IJobStore) -> None:
    global _job_store
    _job_store = store


class RefreshJobResponse(BaseModel):
    job_id: str
    status: str
    steps_taken: Optional[int] = None
    error: Optional[str] = None
    messages: Optional[list] = None


@router.get("/health")
async def health():
    return {"status": "ok", "service": "cookie-refresher"}


@router.post("/refresh", status_code=202, response_model=RefreshJobResponse)
async def trigger_refresh(background_tasks: BackgroundTasks):
    """Enqueue a session-cookie refresh. Returns immediately with a job_id to poll."""
    if _use_case_factory is None or _job_store is None:
        raise HTTPException(status_code=503, detail="Service not initialised")

    job = await _job_store.create()
    background_tasks.add_task(_run_refresh, _use_case_factory, _job_store, job.id)
    return RefreshJobResponse(job_id=job.id, status=job.status.value)


@router.get("/refresh/{job_id}", response_model=RefreshJobResponse)
async def get_refresh_status(job_id: str):
    """Poll the outcome of a previously enqueued refresh job."""
    if _job_store is None:
        raise HTTPException(status_code=503, detail="Service not initialised")

    job = await _job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return RefreshJobResponse(
        job_id=job.id,
        status=job.status.value,
        steps_taken=job.steps_taken,
        error=job.error,
        messages=job.messages or None,
    )


async def _run_refresh(use_case_factory, job_store: IJobStore, job_id: str) -> None:
    if inspect.iscoroutinefunction(use_case_factory):
        use_case = await use_case_factory()
    else:
        use_case = use_case_factory()
    try:
        result: AgentResult = await use_case.execute()
    except Exception as exc:
        logger.exception("Unhandled error in refresh job %s", job_id)
        result = AgentResult.fail(str(exc), steps_taken=0)
    if not result.success:
        logger.warning("Refresh job %s failed: %s", job_id, result.error)
    await job_store.update(job_id, result)
