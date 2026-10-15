from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .agents import QuestionGeneratorAgent, SupervisorAgent
from .schemas import ModelSelection, ReviewDecision
from .state import OrchestrationState


def _now() -> str:
    return datetime.now(UTC).isoformat()


def build_graph(
    *,
    supervisor: SupervisorAgent,
    question_generator: QuestionGeneratorAgent,
    checkpointer: Any,
    store: Any = None,
):
    async def supervisor_node(state: OrchestrationState) -> Command:
        review_action = state.get("review_action")
        if review_action == "approve":
            return Command(
                update={"updated_at": _now(), "error": None},
                goto="retrieval_placeholder",
            )
        if review_action == "reject":
            return Command(
                update={"status": "rejected", "updated_at": _now(), "error": None},
                goto=END,
            )

        if state.get("supervisor_preassessed") and review_action is None:
            return Command(
                update={
                    "status": "generating",
                    "supervisor_preassessed": False,
                    "updated_at": _now(),
                    "error": None,
                },
                goto="question_generator",
            )

        is_regeneration = review_action == "regenerate"
        attempt = int(state.get("attempt", 0)) + (1 if is_regeneration else 0)
        feedback = state.get("review_feedback", "") if is_regeneration else ""
        phase_started = perf_counter()
        decision = await supervisor.delegate(
            source_text=state["source_text"],
            selection=ModelSelection.model_validate(state["supervisor_model"]),
            attempt=attempt,
            feedback=feedback,
        )
        update = {
            "status": "supervising",
            "attempt": attempt,
            "supervisor_instruction": decision.instruction,
            "supervisor_reason": decision.reason,
            "review_action": None,
            "question_generation_ms": round((perf_counter() - phase_started) * 1000),
            "updated_at": _now(),
            "error": None,
        }
        if decision.route == "human_review":
            update["status"] = "awaiting_review"
            update["target_question_id"] = None
            if not is_regeneration:
                update["questions"] = []
            return Command(update=update, goto="human_review")
        return Command(update=update, goto="question_generator")

    async def question_generator_node(state: OrchestrationState) -> Command:
        phase_started = perf_counter()
        target_question_id = state.get("target_question_id")
        prior_questions = state.get("questions", [])
        instruction = state.get("supervisor_instruction", "Extract the questions.")
        if target_question_id:
            prior_questions = [
                item for item in prior_questions if item.get("id") == target_question_id
            ]
            instruction = (
                f"{instruction}\nRegenerate only the selected question. Return exactly "
                "one improved replacement and do not create or alter other questions."
            )
        generated = await question_generator.generate(
            source_text=state["source_text"],
            instruction=instruction,
            selection=ModelSelection.model_validate(state["question_model"]),
            prior_questions=prior_questions,
            session_questions=state.get("session_questions", []),
            relation=state.get("question_relation", "none"),
            parent_question_ids=state.get("parent_question_ids", []),
            standalone_intent=state.get("standalone_intent", ""),
            feedback=state.get("review_feedback", ""),
        )
        generation_ms = int(
            state.get("question_generation_ms") or 0
        ) + round((perf_counter() - phase_started) * 1000)
        if not generated.questions:
            return Command(
                update={
                    "questions": state.get("questions", []),
                    "status": "failed",
                    "target_question_id": None,
                    "question_generation_ms": generation_ms,
                    "review_feedback": "",
                    "updated_at": _now(),
                    "error": "Question generator returned no questions for READY text.",
                },
                goto=END,
            )
        if target_question_id:
            questions = []
            replacement = generated.questions[0] if generated.questions else None
            for item in state.get("questions", []):
                if item.get("id") == target_question_id and replacement is not None:
                    questions.append(
                        {
                            "id": target_question_id,
                            "question": replacement.question,
                            "source_excerpt": replacement.source_excerpt,
                            "review_status": "pending",
                        }
                    )
                else:
                    questions.append(item)
        else:
            questions = [
                {
                    "id": f"q{index}",
                    "question": candidate.question,
                    "source_excerpt": candidate.source_excerpt,
                    "review_status": "pending",
                }
                for index, candidate in enumerate(generated.questions, start=1)
            ]
        update = {
            "questions": questions,
            "status": "awaiting_review",
            "target_question_id": None,
            "question_generation_ms": generation_ms,
            "review_feedback": "",
            "updated_at": _now(),
            "error": None,
        }
        return Command(
            update=update,
            goto="human_review",
        )

    async def human_review_node(state: OrchestrationState) -> Command:
        # Keep interrupt first and this node side-effect free. LangGraph re-runs
        # the node from the beginning when a reviewer resumes the thread.
        decision = interrupt(
            {
                "run_id": state["run_id"],
                "source_text": state["source_text"],
                "project_id": state.get("project_id"),
                "project_name": state.get("project_name"),
                "questions": state.get("questions", []),
                "instruction": "Review, edit, reject, or regenerate each question independently.",
            },
            response_schema=ReviewDecision,
        )
        if not isinstance(decision, ReviewDecision):
            decision = ReviewDecision.model_validate(decision)
        current_statuses = {
            item.get("id"): item.get("review_status", "pending")
            for item in state.get("questions", [])
        }
        questions = []
        for item in decision.questions:
            submitted = item.model_dump(mode="json")
            submitted["review_status"] = current_statuses.get(item.id, "pending")
            questions.append(submitted)

        target = next(
            (item for item in questions if item["id"] == decision.question_id), None
        )
        if target is None:
            raise ValueError("selected question is no longer available")

        if decision.action == "regenerate":
            target["review_status"] = "pending"
            return Command(
                update={
                    "questions": questions,
                    "review_action": "regenerate",
                    "review_feedback": decision.feedback,
                    "target_question_id": decision.question_id,
                    "updated_at": _now(),
                },
                goto="supervisor",
            )

        target["review_status"] = (
            "validated" if decision.action == "approve" else "rejected"
        )
        pending = any(item["review_status"] == "pending" for item in questions)
        if pending:
            return Command(
                update={
                    "questions": questions,
                    "review_action": None,
                    "review_feedback": "",
                    "target_question_id": None,
                    "updated_at": _now(),
                },
                goto="human_review",
            )

        final_action = (
            "approve"
            if any(item["review_status"] == "validated" for item in questions)
            else "reject"
        )
        return Command(
            update={
                "questions": questions,
                "review_action": final_action,
                "review_feedback": "",
                "target_question_id": None,
                "updated_at": _now(),
            },
            goto="supervisor",
        )

    async def retrieval_placeholder_node(state: OrchestrationState) -> dict:
        """Stable handoff seam for the future retrieval agent.

        This phase deliberately performs no retrieval. Approved questions remain
        in graph state and are marked ready for the next implementation phase.
        """
        return {
            "status": "validated",
            "retrieval_status": "ready",
            "updated_at": _now(),
            "error": None,
        }

    builder = StateGraph(OrchestrationState)
    builder.add_node(
        "supervisor",
        supervisor_node,
        destinations={
            "question_generator": "delegate or regenerate",
            "human_review": "review incomplete input",
            "retrieval_placeholder": "validated questions",
            END: "rejected",
        },
    )
    builder.add_node(
        "question_generator",
        question_generator_node,
        destinations={"human_review": "questions generated", END: "no question"},
    )
    builder.add_node(
        "human_review",
        human_review_node,
        destinations={
            "human_review": "next question",
            "supervisor": "approve, reject, or regenerate",
        },
    )
    builder.add_node("retrieval_placeholder", retrieval_placeholder_node)
    builder.add_edge(START, "supervisor")
    builder.add_edge("retrieval_placeholder", END)
    return builder.compile(checkpointer=checkpointer, store=store)

