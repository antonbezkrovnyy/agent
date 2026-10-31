"""LLM и схемы структурированных результатов агентов."""

from datetime import datetime
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


class WindowSpec(BaseModel):
    """Запрошенный период до детерминированной фиксации границ."""

    time_from: datetime | None = Field(default=None, description="Начало ISO-8601, если задано явно")
    time_to: datetime | None = Field(default=None, description="Конец ISO-8601, если задан явно")
    last_minutes: int = Field(default=60, gt=0, description="Относительный период; 60 для последнего часа")


class IntentDecision(BaseModel):
    """Маршрутизация и временной контекст запроса."""

    intent: Literal["simple", "complex", "continuation"] = Field(
        description="simple — беседа; complex — новый анализ; continuation — фолло-ап"
    )
    rationale: str = Field(description="Обоснование одним предложением")
    needs_new_data: bool = Field(
        default=False,
        description="true, если нужны новые запросы к журналу; false для беседы и работы с готовыми данными",
    )
    edit_existing_report: bool = Field(
        default=False,
        description="true только для правки существующего отчёта без нового расследования",
    )
    window: WindowSpec = Field(default_factory=WindowSpec)


class Claim(BaseModel):
    """Утверждение с обязательными ссылками на фактические вызовы инструментов."""

    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class ScoutResult(BaseModel):
    note: str = Field(description="Короткий ориентир для инвестигейтора")
    signals: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class InvestigationResult(BaseModel):
    summary: str
    claims: list[Claim] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ReporterResult(BaseModel):
    answer: str = Field(description="Финальный ответ пользователю на русском")
    claims: list[Claim] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class CriticEvidence(BaseModel):
    """Вердикт критика по данным расследования."""

    verdict: Literal["ok", "rework_data"] = Field(
        description="ok — улик достаточно; rework_data — не хватает"
    )
    feedback: str = Field(description="Что доискать; пусто при ok")


class CriticReport(BaseModel):
    """Вердикт критика по отчёту."""

    verdict: Literal["ok", "rework_report", "rework_data"] = Field(
        description=(
            "ok — отчёт готов; rework_report — править сам отчёт; "
            "rework_data — данных не хватает, доискать у инвестигейтора"
        )
    )
    feedback: str = Field(description="Что исправить или доискать; пусто при ok")


def build_intent_llm(model):
    """Function calling с автоматическим переходом на JSON mode."""
    return model.with_structured_output(
        IntentDecision,
        method="function_calling",
    ).with_fallbacks([
        model.with_structured_output(IntentDecision, method="json_mode")
    ])


intent_llm = build_intent_llm(llm)
critic_evidence_llm = llm.with_structured_output(CriticEvidence, method="function_calling")
critic_report_llm = llm.with_structured_output(CriticReport, method="function_calling")
