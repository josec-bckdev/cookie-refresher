"""FastAPI controller — health check + manual trigger endpoint."""
import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# Injected at startup by infrastructure/main.py
_use_case_factory = None


def set_use_case_factory(factory) -> None:
    global _use_case_factory
    _use_case_factory = factory


class RefreshResponse(BaseModel):
    success: bool
    steps_taken: int
    error: str | None = None


@router.get("/health")
async def health():
    return {"status": "ok", "service": "cookie-refresher"}


@router.post("/refresh", response_model=RefreshResponse)
async def trigger_refresh(background_tasks: BackgroundTasks):
    """
    Manually trigger a session-cookie refresh.
    The agent runs in the background; the response is the *scheduling* confirmation.
    Use GET /refresh/status (future) to poll the outcome.
    """
    if _use_case_factory is None:
        raise HTTPException(status_code=503, detail="Use case not initialised")

    use_case = _use_case_factory()
    result = await use_case.execute()

    if not result.success:
        logger.warning("Manual refresh failed: %s", result.error)

    return RefreshResponse(
        success=result.success,
        steps_taken=result.steps_taken,
        error=result.error,
    )
