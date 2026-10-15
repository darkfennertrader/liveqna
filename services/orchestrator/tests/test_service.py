import pytest
import asyncio

from app.schemas import (
    AutomaticCandidateRequest,
    CreateRunRequest,
    ModelSelection,
    TranscriptReadiness,
)
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


class FakeSupervisor:
    def __init__(self, decision: str = "ready") -> None:
        self.decision = decision
        self.calls = []

    async def assess(self, **kwargs) -> TranscriptReadiness:
        self.calls.append(kwargs)
        return TranscriptReadiness(
            decision=self.decision,
            instruction="Generate only supported questions.",
            reason="Test readiness decision.",
            confidence=0.9,
        )


class FollowUpSupervisor(FakeSupervisor):
    async def assess(self, **kwargs) -> TranscriptReadiness:
        self.calls.append(kwargs)
        return TranscriptReadiness(
            decision="ready",
            relation="follow_up",
            parent_question_ids=["previous-run:q1"],
            standalone_intent="Does the retention rule also apply to companies?",
            instruction="Generate the standalone follow-up question.",
            reason="The current request extends the previous question.",
            confidence=0.95,
        )


class FakeQuestionMemory:
    def __init__(self) -> None:
        self.recent = [
            {
                "memory_id": "previous-run:q1",
                "question": "What is the retention period?",
                "review_status": "validated",
            }
        ]
        self.closed: list[str] = []

    async def recent_questions(self, session_id: str) -> list[dict]:
        return self.recent

    def enqueue_snapshot(self, run) -> None:
        pass

    async def close_session(self, session_id: str) -> None:
        self.closed.append(session_id)

    async def clear_all(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass


class BlockingGraph(FakeGraph):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.initial_state = None

    async def ainvoke(self, state, config):
        self.initial_state = state
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
    service = OrchestrationService(
        graph=graph, repository=repository, supervisor=FakeSupervisor()
    )

    cleared = await service.clear_runs()

    assert cleared == 2
    assert graph.checkpointer.deleted_threads == ["run-1", "run-2"]
    assert repository.deleted is True


@pytest.mark.asyncio
async def test_enqueue_run_returns_before_graph_finishes() -> None:
    repository = EnqueueRepository()
    graph = BlockingGraph()
    service = OrchestrationService(
        graph=graph, repository=repository, supervisor=FakeSupervisor()
    )
    selection = ModelSelection(provider="openai", model="test")

    accepted = await service.enqueue_run(
        CreateRunRequest(
            text="What changed?",
            project_id="project-1",
            project_name="Client A",
            supervisor_model=selection,
            question_model=selection,
        )
    )
    await asyncio.wait_for(graph.started.wait(), timeout=1)

    assert accepted.accepted is True
    assert accepted.status == "generating"
    assert repository.created[0][0] == accepted.run_id
    assert graph.initial_state["project_id"] == "project-1"
    assert graph.initial_state["project_name"] == "Client A"

    await service.shutdown()


@pytest.mark.asyncio
async def test_ready_candidate_reuses_supervisor_assessment_in_graph_state() -> None:
    repository = EnqueueRepository()
    graph = BlockingGraph()
    supervisor = FakeSupervisor("ready")
    service = OrchestrationService(
        graph=graph, repository=repository, supervisor=supervisor
    )
    selection = ModelSelection(provider="openai", model="test")

    response = await service.assess_candidate(
        AutomaticCandidateRequest(
            text="Could you explain the cost change?",
            session_id="session-1",
            project_id="project-1",
            project_name="Client A",
            supervisor_model=selection,
            question_model=selection,
        )
    )
    await asyncio.wait_for(graph.started.wait(), timeout=1)

    assert response.decision == "ready"
    assert response.run_id is not None
    assert graph.initial_state["supervisor_preassessed"] is True
    assert graph.initial_state["supervisor_instruction"] == response.instruction
    assert graph.initial_state["question_generation_ms"] >= 0

    await service.shutdown()


@pytest.mark.asyncio
async def test_wait_candidate_does_not_create_a_run() -> None:
    repository = EnqueueRepository()
    graph = BlockingGraph()
    service = OrchestrationService(
        graph=graph, repository=repository, supervisor=FakeSupervisor("wait")
    )
    selection = ModelSelection(provider="openai", model="test")

    response = await service.assess_candidate(
        AutomaticCandidateRequest(
            text="Could you explain...",
            session_id="session-1",
            project_id="project-1",
            project_name="Client A",
            supervisor_model=selection,
            question_model=selection,
        )
    )

    assert response.decision == "wait"
    assert response.run_id is None
    assert repository.created == []


@pytest.mark.asyncio
async def test_recent_session_questions_drive_follow_up_state() -> None:
    repository = EnqueueRepository()
    graph = BlockingGraph()
    memory = FakeQuestionMemory()
    supervisor = FollowUpSupervisor()
    service = OrchestrationService(
        graph=graph,
        repository=repository,
        supervisor=supervisor,
        question_memory=memory,
    )
    selection = ModelSelection(provider="openai", model="test")

    response = await service.assess_candidate(
        AutomaticCandidateRequest(
            text="And does that also apply to companies?",
            session_id="session-1",
            project_id="project-1",
            project_name="Client A",
            supervisor_model=selection,
            question_model=selection,
        )
    )
    await asyncio.wait_for(graph.started.wait(), timeout=1)

    assert supervisor.calls[0]["recent_questions"] == memory.recent
    assert response.relation == "follow_up"
    assert graph.initial_state["session_id"] == "session-1"
    assert graph.initial_state["session_questions"] == memory.recent
    assert graph.initial_state["parent_question_ids"] == ["previous-run:q1"]
    assert graph.initial_state["standalone_intent"] == (
        "Does the retention rule also apply to companies?"
    )

    await service.end_session("session-1")
    assert memory.closed == ["session-1"]
    await service.shutdown()
