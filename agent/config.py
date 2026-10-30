from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict



class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra='ignore'                   # <-- ИБ- best practice: игнорировать случайные env-переменные из окружения контейнера
    )

    llm_model: str = "qwen3.7-flash"
    llm_base_url: str
    llm_token: str

    # Logicore MCP (streamable-http в отдельном контейнере logicore-mcp, ADR-0010)
    logicore_mcp_url: str = Field(
        default="http://logicore-mcp:9000/mcp",
        validation_alias=AliasChoices("LOGICORE_MCP_URL", "LOGIQ_MCP_URL"),
    )
    logicore_mcp_token: str = Field(
        default="",
        validation_alias=AliasChoices("LOGICORE_MCP_TOKEN", "LOGIQ_API_TOKEN"),
    )
