import pytest
import asyncio

from app.schemas import CreateRunRequest, ModelSelection
from app.service import OrchestrationService


class FakeRepository:
    def __init__(self) -> None:
        self.deleted = False

    async def list_ids(self, limit=None):
        assert limit is None
        return ["run-1", "run-2"]

    async def delete_all(self) -> None:
        self.deleted = True


class FakeCheckpointer:
    def __init__(self) -> None:
        self.deleted_threads = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted_threads.append(thread_id)


class FakeGraph:
    def __init__(self) -> None:
        self.checkpointer = FakeCheckpointer()


class BlockingGraph(FakeGraph):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def ainvoke(self, state, config):
        self.started.set()
        await self.release.wait()


class EnqueueRepository:
    def __init__(self) -> None:
        self.created = []

    async def create(self, run_id: str, created_at: str) -> None:
        self.created.append((run_id, created_at))


@pytest.mark.asyncio
async def test_clear_runs_deletes_registry_and_checkpoints() -> None:
    repository = FakeRepository()
    graph = FakeGraph()
    service = OrchestrationService(graph=graph, repository=repository)

    cleared = await service.clear_runs()

    assert cleared == 2
    assert graph.checkpointer.deleted_threads == ["run-1", "run-2"]
    assert repository.deleted is True


@pytest.mark.asyncio
async def test_enqueue_run_returns_before_graph_finishes() -> None:
    repository = EnqueueRepository()
    graph = BlockingGraph()
    service = OrchestrationService(graph=graph, repository=repository)
    selection = ModelSelection(provider="openai", model="test")

    accepted = await service.enqueue_run(
        CreateRunRequest(
            text="What changed?",
            supervisor_model=selection,
            question_model=selection,
        )
    )
    await asyncio.wait_for(graph.started.wait(), timeout=1)

    assert accepted.accepted is True
    assert accepted.status == "generating"
    assert repository.created[0][0] == accepted.run_id

    await service.shutdown()
