from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict



class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra='ignore'                   # <-- ИБ- best practice: игнорировать случайные env-переменные из окружения контейнера
    )

    llm_model: str = "qwen3.7-flash"
    llm_model_intent: str = ""
    llm_model_scout: str = ""
    llm_model_investigator: str = ""
    llm_model_reporter: str = ""
    llm_model_critic: str = ""
    llm_model_memory: str = ""
    llm_model_planner: str = ""
    llm_base_url: str
    llm_token: str

    summarization_trigger_tokens: int = 230000
    summarization_keep_messages: int = 20

    # Logicore MCP (streamable-http в отдельном контейнере logicore-mcp, ADR-0010)
    logicore_mcp_url: str = Field(
        default="http://logicore-mcp:9000/mcp",
        validation_alias=AliasChoices("LOGICORE_MCP_URL", "LOGIQ_MCP_URL"),
    )
    logicore_mcp_token: str = Field(
        default="",
        validation_alias=AliasChoices("LOGICORE_MCP_TOKEN", "LOGIQ_API_TOKEN"),
    )

    # Критик: проверка улик после инвестигейтора и отчёта после репортёра
    critic_enabled: bool = True
    # Сколько раз за run критик может отправить на доработку (улики + отчёт, суммарно)
    max_rework: int = 1
    critic_message_limit: int = 2000
    critic_dialog_limit: int = 8000
    report_inject_limit: int = 8000

    # Лимиты одного create_agent.ainvoke, а не всего чата
    agent_tool_limit_scout: int = 10
    agent_tool_limit_investigator: int = 30
    agent_tool_limit_reporter: int = 60
    # Автоматические продолжения роли внутри одного work item после run_limit
    agent_max_continuations: int = 1
    investigation_planner_enabled: bool = True
    planner_max_items: int = 5
    planner_max_parallel: int = 2
    planner_max_revisions: int = 2
    scope_discovery_max_items: int = 2
    scope_required_max_items: int = 2
    agent_prompt_version: str = "2026-11-07.8"

    # Отдельные Case Store и Context Memory, не БД Aegra checkpoints
    agent_memory_database_url: str = ""
    agent_memory_recent_limit: int = 3
    agent_memory_scope: str = "default"
    agent_memory_retrieval_limit: int = 5
    agent_memory_case_retrieval_limit: int = 3
    agent_memory_consolidation_candidates: int = 10
    agent_memory_max_operations: int = 8
    agent_memory_similarity_threshold: float = 0.10
    agent_memory_dedup_threshold: float = 0.90
    agent_memory_observed_ttl_hours: int = 24
    agent_memory_embedding_model: str = "bge-m3"
    agent_memory_embedding_dimensions: int = 1024
    agent_memory_embedding_base_url: str = ""
    agent_memory_embedding_token: str = ""

    # Технические таймауты от зависаний; пользователь не управляет ими
    agent_finalize_after_seconds: int = 600
    agent_finalize_attempts: int = 10
    agent_finalize_attempt_timeout_seconds: int = 300
    agent_timeout_seconds: int = 900
    agent_run_timeout_seconds: int = 1800
    llm_request_timeout_seconds: int = 300
    intent_new_investigation_min_confidence: float = 0.75
    export_timeout_seconds: int = 120

    # Изолированный SQL-анализ полных выгрузок, без исполнения кода в Aegra
    analysis_service_url: str = "http://analysis-service:9200"
    analysis_service_token: str = ""
    analysis_timeout_seconds: int = 120
    analysis_dataset_max_rows: int = 20000
    analysis_result_max_rows: int = 10000
    analysis_sql_max_attempts: int = 6

    # Файлы расследований: S3 в работе, data-URL только для локального fallback
    artifact_store_backend: str = "auto"
    artifact_s3_endpoint: str = ""
    artifact_s3_public_endpoint: str = ""
    artifact_s3_region: str = "us-east-1"
    artifact_s3_bucket: str = "logicore-investigations"
    artifact_s3_access_key: str = ""
    artifact_s3_secret_key: str = ""
    artifact_s3_prefix: str = "investigations"
    artifact_signed_url_ttl_seconds: int = 3600
    artifact_inline_fallback_bytes: int = 262144
