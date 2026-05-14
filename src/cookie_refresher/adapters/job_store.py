import uuid

from cookie_refresher.domain.entities import AgentResult, Job, JobStatus
from cookie_refresher.domain.ports import IJobStore


class InMemoryJobStore(IJobStore):
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    async def create(self) -> Job:
        job = Job(id=str(uuid.uuid4()), status=JobStatus.PROCESSING)
        self._jobs[job.id] = job
        return job

    async def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    async def update(self, job_id: str, result: AgentResult) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.status = JobStatus.SUCCESS if result.success else JobStatus.FAILED
        job.steps_taken = result.steps_taken
        job.error = result.error
        job.mode = result.mode
        job.failure_reason = result.failure_reason
        job.messages = result.messages
