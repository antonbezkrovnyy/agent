"""Конфигурация процесса: креды, TLS, таймауты, дефолтная проекция (ADR-0003, TD-0002, TD-0007)."""

from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_DEFAULT_COLUMNS = (
    "uuid",
    "timestamp",
    "event_type",
    "action",
    "category_low",
    "subject",
    "object",
    "src_host",
    "src_ip",
    "dst_host",
    "dst_ip",
    "generator",
    "recv_ipv4",
    "mime",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOGIQ_", extra="ignore")

    url: str
    user: str
    password: str
    tls_verify: bool = True
    timeout_connect: float = 5.0
    timeout_read: float = 30.0
    earth_concurrency: int = 1
    split_min_span_s: float = 3600.0
    split_budget_s: float = 600.0
    ws_url: str | None = None
    ws_wait: float = 30.0
    log_file: str | None = None
    fake_earth: str | None = None
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 9000
    api_token: str | None = None
    auth: str = "token"
    issuer_url: str | None = None
    rate_limit_calls: int = 300
    rate_limit_window_s: int = 600
    extract_max_rows: int = 1000
    tenant_ids: Annotated[tuple[int, ...], NoDecode] = ()
    default_columns: Annotated[tuple[str, ...], NoDecode] = _DEFAULT_COLUMNS

    @field_validator("url")
    @classmethod
    def _url_scheme(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("LOGIQ_URL должен начинаться с http:// или https://")
        return value.rstrip("/")

    @field_validator("fake_earth")
    @classmethod
    def _fake_earth_notes(cls, value: str | None) -> str | None:
        """LOGIQ_FAKE_EARTH — red-team профиль (TD-0040): url/user/password фиктивны, стенд не касается."""
        if value:
            import warnings

            warnings.warn(
                "LOGIQ_FAKE_EARTH включён: сервер отвечает фикстурой red-team, реальные данные недоступны (TD-0040)",
                stacklevel=2,
            )
        return value

    @field_validator("transport")
    @classmethod
    def _transport_values(cls, value: str) -> str:
        """LOGIQ_TRANSPORT — stdio (умолчание, ADR-0002) | streamable-http (ADR-0010)."""
        if value not in ("stdio", "streamable-http"):
            raise ValueError("LOGIQ_TRANSPORT должен быть stdio или streamable-http (ADR-0010)")
        return value

    @field_validator("auth")
    @classmethod
    def _auth_values(cls, value: str) -> str:
        """LOGIQ_AUTH — none | token (ADR-0010) | oauth (ADR-0011)."""
        if value not in ("none", "token", "oauth"):
            raise ValueError("LOGIQ_AUTH должен быть none, token или oauth (ADR-0010/0011)")
        return value

    @model_validator(mode="after")
    def _transport_auth_rules(self) -> "Settings":
        """Громкие отказы для streamable-http: без авторизации не стартуем (ADR-0010/0011)."""
        if self.transport == "streamable-http":
            if self.auth == "none":
                raise ValueError("LOGIQ_AUTH=none недопустим для streamable-http (ADR-0010)")
            if self.auth == "token" and not self.api_token:
                raise ValueError("LOGIQ_API_TOKEN обязателен для LOGIQ_AUTH=token (ADR-0010)")
            if self.auth == "oauth" and not self.issuer_url:
                raise ValueError("LOGIQ_ISSUER_URL обязателен для LOGIQ_AUTH=oauth (ADR-0011)")
        return self

    @field_validator("tenant_ids", mode="before")
    @classmethod
    def _parse_tenant_ids(cls, value: object) -> object:
        """LOGIQ_TENANT_IDS — целые через запятую: ограничение запросов тенантами конфига (ADR-0007)."""
        if isinstance(value, str):
            parts = tuple(int(part.strip()) for part in value.split(",") if part.strip())
            return parts
        return value

    @field_validator("default_columns", mode="before")
    @classmethod
    def _parse_columns(cls, value: object) -> object:
        """LOGIQ_DEFAULT_COLUMNS — имена через запятую (TD-0007)."""
        if isinstance(value, str):
            parts = tuple(part.strip() for part in value.split(",") if part.strip())
            if not parts:
                raise ValueError("LOGIQ_DEFAULT_COLUMNS: укажите хотя бы одну колонку через запятую")
            return parts
        return value


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # обязательные поля приходят из env/.env (pydantic-settings)
