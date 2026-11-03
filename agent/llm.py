"""LLM и схемы структурированных результатов агентов."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from langchain.chat_models import init_chat_model

from config import Settings
from contracts import Coverage, CriticIssue, RecommendedExportQuery
from skills.contracts import Anchor, SkillRunResult

settings = Settings()

llm = init_chat_model(
    model_provider="openai",
    model=settings.llm_model,
    base_url=settings.llm_base_url,
    api_key=settings.llm_token,
    temperature=0.0,
    max_retries=3,
)


def model_name_for_role(role: str) -> str:
    return getattr(settings, f"llm_model_{role}", "") or settings.llm_model


def model_for_role(role: str):
    name = model_name_for_role(role)
    if name == settings.llm_model:
        return llm
    return init_chat_model(
        model_provider="openai",
        model=name,
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
    work_mode: Literal["quick", "deep"] = Field(
        default="quick",
        description="quick — ограниченный ответ; deep — причины, первое появление, pivot или смена окна",
    )
    rationale: str = Field(description="Обоснование одним предложением")
    needs_new_data: bool = Field(
        default=False,
        description="true, если нужны новые запросы к журналу; false для беседы и работы с готовыми данными",
    )
    edit_existing_report: bool = Field(
        default=False,
        description="true для создания, правки или доставки аналитического отчёта без нового расследования",
    )
    export_data: bool = Field(
        default=False,
        description="true только для выгрузки сырых событий таблицей/файлом; никогда для отчёта расследования",
    )
    new_investigation: Literal["continue", "new", "uncertain"] = Field(
        default="continue",
        description="continue — та же задача; new — другая задача; uncertain — продолжить без вопроса",
    )
    window_requested: bool = Field(
        default=False,
        description="true только если пользователь явно указал или изменил временной период",
    )
    skill_id: Literal["incident_dating", "event_drilldown", "entity_pivot"] | None = Field(
        default=None,
        description="incident_dating — первое появление; event_drilldown — одно событие; entity_pivot — связанные сущности",
    )
    related_investigation_id: str | None = Field(
        default=None,
        description="ID случая из memory_candidates, если пользователь явно продолжает его в новом треде",
    )
    confidence: float = Field(default=1.0, ge=0, le=1, description="Уверенность классификатора от 0 до 1")
    escalation_reason: str = Field(default="", description="Почему выбран deep вместо quick; пусто для quick")
    window: WindowSpec = Field(default_factory=WindowSpec)


class WindowUpdate(BaseModel):
    """Автономное изменение окна без создания нового расследования."""

    action: Literal["preserve", "focus", "expand", "replace"] = Field(
        description="focus — временный фокус; expand — расширить base; replace — заменить base; preserve — не менять"
    )
    time_from: datetime | None = Field(default=None, description="Новое начало ISO-8601")
    time_to: datetime | None = Field(default=None, description="Новый конец ISO-8601")
    reason: str = Field(description="Короткая причина изменения окна")


class Claim(BaseModel):
    """Утверждение с обязательными ссылками на фактические вызовы инструментов."""

    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class Escalation(BaseModel):
    needed: bool = Field(default=False, description="true, если quick-проверки недостаточно")
    reason: str = Field(default="", description="Короткая причина перехода к deep")
    suggested_skill: str | None = Field(default=None, description="Необязательный skill для deep")


class ScoutResult(BaseModel):
    note: str = Field(default="", description="Короткий результат быстрой проверки")
    answer_candidate: str = Field(default="", description="Кандидат в ограниченный ответ без deep")
    signals: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    anchors: list[Anchor] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    escalation: Escalation = Field(default_factory=Escalation)


class InvestigationResult(BaseModel):
    status: Literal["done", "partial", "failed"] = "done"
    summary: str
    claims: list[Claim] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    not_checked: list[str] = Field(default_factory=list, description="Что не проверено и почему")
    recommended_export_query: RecommendedExportQuery | None = None
    skill_run: SkillRunResult | None = Field(
        default=None,
        description="Фактический результат активного skill; null, если skill не выбран",
    )


class ReporterResult(BaseModel):
    answer: str = Field(description="Финальный ответ пользователю на русском")
    claims: list[Claim] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    not_checked: list[str] = Field(default_factory=list, description="Что не проверено и почему")
    recommended_export_query: RecommendedExportQuery | None = None


class CriticEvidence(BaseModel):
    """Вердикт критика по данным расследования."""

    verdict: Literal["ok", "rework_data"] = Field(
        description="ok — улик достаточно; rework_data — не хватает"
    )
    feedback: str = Field(description="Что доискать; пусто при ok")
    issues: list[CriticIssue] = Field(default_factory=list)


class CriticReport(BaseModel):
    """Вердикт критика по отчёту."""

    verdict: Literal["ok", "rework_report", "rework_data"] = Field(
        description=(
            "ok — отчёт готов; rework_report — править сам отчёт; "
            "rework_data — данных не хватает, доискать у инвестигейтора"
        )
    )
    feedback: str = Field(description="Что исправить или доискать; пусто при ok")
    issues: list[CriticIssue] = Field(default_factory=list)


def build_intent_llm(model):
    """Function calling с автоматическим переходом на JSON mode."""
    return model.with_structured_output(
        IntentDecision,
        method="function_calling",
    ).with_fallbacks([
        model.with_structured_output(IntentDecision, method="json_mode")
    ])


def build_context_memory_llm(model):
    from memory_models import ContextMemoryConsolidation

    return model.with_structured_output(
        ContextMemoryConsolidation,
        method="function_calling",
    ).with_fallbacks([
        model.with_structured_output(ContextMemoryConsolidation, method="json_mode")
    ])


intent_llm = build_intent_llm(model_for_role("intent"))
critic_evidence_llm = model_for_role("critic").with_structured_output(CriticEvidence, method="function_calling")
critic_report_llm = model_for_role("critic").with_structured_output(CriticReport, method="function_calling")
context_memory_llm = build_context_memory_llm(model_for_role("memory"))
