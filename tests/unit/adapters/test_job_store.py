"""Tests for InMemoryJobStore."""
import pytest
from cookie_refresher.adapters.job_store import InMemoryJobStore
from cookie_refresher.domain.entities import AgentResult, JobStatus, SessionCookies

COOKIES = SessionCookies(cf_clearance="cf_tok", ci_session="ci_tok")


class TestInMemoryJobStore:
    @pytest.fixture
    def store(self):
        return InMemoryJobStore()

    @pytest.mark.asyncio
    async def test_create_returns_job_with_processing_status(self, store):
        job = await store.create()
        assert job.status == JobStatus.PROCESSING
        assert job.id

    @pytest.mark.asyncio
    async def test_create_assigns_unique_ids(self, store):
        a = await store.create()
        b = await store.create()
        assert a.id != b.id

    @pytest.mark.asyncio
    async def test_get_returns_none_for_unknown_id(self, store):
        result = await store.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_job_after_create(self, store):
        job = await store.create()
        found = await store.get(job.id)
        assert found is not None
        assert found.id == job.id

    @pytest.mark.asyncio
    async def test_update_sets_success_status(self, store):
        job = await store.create()
        agent_result = AgentResult.ok(COOKIES, steps_taken=5)
        await store.update(job.id, agent_result)

        updated = await store.get(job.id)
        assert updated.status == JobStatus.SUCCESS
        assert updated.steps_taken == 5
        assert updated.error is None

    @pytest.mark.asyncio
    async def test_update_sets_failed_status(self, store):
        job = await store.create()
        agent_result = AgentResult.fail("Max steps exceeded", steps_taken=100)
        await store.update(job.id, agent_result)

        updated = await store.get(job.id)
        assert updated.status == JobStatus.FAILED
        assert updated.steps_taken == 100
        assert updated.error == "Max steps exceeded"

    @pytest.mark.asyncio
    async def test_update_unknown_job_is_a_noop(self, store):
        agent_result = AgentResult.ok(COOKIES, steps_taken=3)
        await store.update("ghost-id", agent_result)

    @pytest.mark.asyncio
    async def test_update_stores_messages_on_job(self, store):
        job = await store.create()
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        agent_result = AgentResult.ok(COOKIES, steps_taken=2, messages=msgs)
        await store.update(job.id, agent_result)

        updated = await store.get(job.id)
        assert updated.messages == msgs
