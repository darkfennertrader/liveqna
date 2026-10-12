from __future__ import annotations

from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from .model_factory import ModelFactory
from .schemas import (
    GeneratedQuestionSet,
    ModelSelection,
    SupervisorDecision,
)


SUPERVISOR_SYSTEM_PROMPT = """You supervise question preparation for a live Q&A workflow.
You receive verbatim speech-to-text captured by a user. Do not answer the text.
Delegate usable text to the question generator. Route directly to human review only
when the input is too fragmentary to extract a responsible question. Give the worker
a concise instruction that preserves the speaker's language, entities, qualifiers,
timeframes, and intended information need. Never invent missing business facts."""


QUESTION_GENERATOR_SYSTEM_PROMPT = """You turn live speech transcripts into questions
that are suitable for high-quality retrieval. Do not answer the questions.

Rules:
- Extract explicit questions and clear implicit requests for information.
- Split independent information needs into separate questions.
- Make each question self-contained using only referents present in the transcript.
- Preserve names, metrics, dates, timeframes, constraints, and the source language.
- Remove speech disfluencies without changing intent.
- Do not add assumptions, topics, or facts absent from the source.
- Deduplicate overlapping questions.
- Include a short verbatim source excerpt supporting each question.
- Return an empty list when no responsible question can be derived."""


class SupervisorAgent(Protocol):
    async def delegate(
        self,
        *,
        source_text: str,
        selection: ModelSelection,
        attempt: int,
        feedback: str,
    ) -> SupervisorDecision: ...


class QuestionGeneratorAgent(Protocol):
    async def generate(
        self,
        *,
        source_text: str,
        instruction: str,
        selection: ModelSelection,
        prior_questions: list[dict],
        feedback: str,
    ) -> GeneratedQuestionSet: ...


class LLMSupervisorAgent:
    def __init__(self, models: ModelFactory) -> None:
        self._models = models

    async def delegate(
        self,
        *,
        source_text: str,
        selection: ModelSelection,
        attempt: int,
        feedback: str,
    ) -> SupervisorDecision:
        model = self._models.structured(selection, SupervisorDecision)
        prompt = (
            f"Captured text:\n{source_text}\n\n"
            f"Generation attempt: {attempt}.\n"
            f"Human regeneration guidance: {feedback or '(none)'}"
        )
        result = await model.ainvoke(
            [SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT), HumanMessage(content=prompt)]
        )
        return result if isinstance(result, SupervisorDecision) else SupervisorDecision.model_validate(result)


class LLMQuestionGeneratorAgent:
    def __init__(self, models: ModelFactory) -> None:
        self._models = models

    async def generate(
        self,
        *,
        source_text: str,
        instruction: str,
        selection: ModelSelection,
        prior_questions: list[dict],
        feedback: str,
    ) -> GeneratedQuestionSet:
        model = self._models.structured(selection, GeneratedQuestionSet)
        prior = "\n".join(
            f"- {item.get('question', '')}" for item in prior_questions
        ) or "(none)"
        prompt = (
            f"Supervisor instruction:\n{instruction}\n\n"
            f"Captured text:\n{source_text}\n\n"
            f"Previous draft questions:\n{prior}\n\n"
            f"Human regeneration guidance: {feedback or '(none)'}"
        )
        result = await model.ainvoke(
            [
                SystemMessage(content=QUESTION_GENERATOR_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]
        )
        return result if isinstance(result, GeneratedQuestionSet) else GeneratedQuestionSet.model_validate(result)

