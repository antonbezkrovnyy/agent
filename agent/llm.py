"""LLM и связанные с ней схемы.

Одна базовая модель для всех агентов. Интент и критик — структурированный
вывод через function_calling: эндпоинт OpenAI-совместимый, а tool-calls
уже проверены в проде на MCP-тулзах.
"""

from typing import Literal

from pydantic import BaseModel, Field

from langchain.chat_models import init_chat_model

from config import Settings

settings = Settings()

llm = init_chat_model(
    model_provider="openai",
    model=settings.llm_model,
    base_url=settings.llm_base_url,
    api_key=settings.llm_token,
    temperature=0.0,
    max_retries=3,
)


class IntentDecision(BaseModel):
    """Результат классификации интента."""

    intent: Literal["simple", "complex", "continuation"] = Field(
        description=(
            "Категория: simple — простой вопрос без данных из журнала; "
            "complex — нужен анализ данных; continuation — продолжение "
            "уже сделанного (правки отчёта, углубление)"
        ),
    )
    rationale: str = Field(description="Обоснование одним предложением")


class CriticEvidence(BaseModel):
    """Вердикт критика по данным расследования."""

    verdict: Literal["ok", "rework_data"] = Field(
        description="ok — улик достаточно; rework_data — не хватает"
    )
    feedback: str = Field(description="Что доискать; пусто при ok")


class CriticReport(BaseModel):
    """Вердикт критика по отчёту."""

    verdict: Literal["ok", "rework_report"] = Field(
        description="ok — отчёт готов; rework_report — отправить на доработку"
    )
    feedback: str = Field(description="Что исправить; пусто при ok")


intent_llm = llm.with_structured_output(IntentDecision, method="function_calling")
critic_evidence_llm = llm.with_structured_output(CriticEvidence, method="function_calling")
critic_report_llm = llm.with_structured_output(CriticReport, method="function_calling")
