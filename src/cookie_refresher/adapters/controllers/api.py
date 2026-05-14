"""FastAPI controller — health check + manual trigger + status endpoints."""
import inspect
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from cookie_refresher.domain.entities import AgentResult, FailureReason
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
    """Response body for job creation and status polling."""

    job_id: str = Field(description="Unique identifier for this refresh job. Use it to poll /refresh/{job_id}.")
    status: str = Field(description="Current job state: 'processing' while running, 'success' or 'failed' when done.")
    mode: Optional[str] = Field(default=None, description="Execution mode: 'agent' (full ReAct loop) or 'replay' (recorded script). Null while processing.")
    steps_taken: Optional[int] = Field(default=None, description="Number of browser actions executed. Null while processing.")
    error: Optional[str] = Field(default=None, description="Human-readable failure description. Null on success.")
    failure_reason: Optional[str] = Field(default=None, description=(
        "Machine-readable failure code. One of: "
        "'no_cookies' (agent finished but found no cookies), "
        "'vtrack_post_failed' (cookies extracted but vtrack rejected them), "
        "'max_steps_exceeded' (agent hit the step limit without finishing), "
        "'exception' (unexpected error). Null on success."
    ))
    messages: Optional[list] = Field(default=None, description="Redacted Claude conversation transcript. Only present on full agent runs.")


@router.get(
    "/health",
    summary="Health check",
    description="Returns 200 while the service is up. Used by Docker and load balancers.",
)
async def health():
    return {"status": "ok", "service": "cookie-refresher"}


@router.post(
    "/refresh",
    status_code=202,
    response_model=RefreshJobResponse,
    summary="Trigger a session cookie refresh",
    description=(
        "Enqueues a cookie refresh job and returns immediately with a `job_id`. "
        "Poll `GET /refresh/{job_id}` to check the outcome. "
        "The job runs in the background: in **replay** mode if a recorded script exists "
        "(fast, ~25s, one Claude API call), or in full **agent** mode otherwise (~2min, ~30 Claude API calls)."
    ),
    response_description="Job accepted. Poll the returned job_id for the result.",
)
async def trigger_refresh(background_tasks: BackgroundTasks):
    if _use_case_factory is None or _job_store is None:
        raise HTTPException(status_code=503, detail="Service not initialised")

    job = await _job_store.create()
    background_tasks.add_task(_run_refresh, _use_case_factory, _job_store, job.id)
    return RefreshJobResponse(job_id=job.id, status=job.status.value)


@router.get(
    "/refresh/{job_id}",
    response_model=RefreshJobResponse,
    summary="Poll a refresh job",
    description=(
        "Returns the current state of a previously enqueued refresh job. "
        "Keep polling until `status` is `'success'` or `'failed'`. "
        "On failure, inspect `failure_reason` for a machine-readable code and `error` for a human-readable description."
    ),
    response_description="Current job state. Re-poll if status is 'processing'.",
)
async def get_refresh_status(job_id: str):
    if _job_store is None:
        raise HTTPException(status_code=503, detail="Service not initialised")

    job = await _job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return RefreshJobResponse(
        job_id=job.id,
        status=job.status.value,
        mode=job.mode,
        steps_taken=job.steps_taken,
        error=job.error,
        failure_reason=job.failure_reason,
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
        result = AgentResult.fail(str(exc), steps_taken=0, failure_reason=FailureReason.EXCEPTION)
    if not result.success:
        logger.warning("Refresh job %s failed: %s", job_id, result.error)
    await job_store.update(job_id, result)
