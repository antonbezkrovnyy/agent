"""OAuth 2.1 authorization server: провайдер с состоянием в памяти (ADR-0011).

Клиенты/коды/токены — opaque, в памяти процесса: рестарт обнуляет (перелогин).
Аутентификация пользователя — реальным Earth-логином: Earth остаётся источником
истины валидности, зеркала пользователей нет. Пользовательские Earth-клиенты
регистрируются в runtime и переживают до рестарта процесса.
"""

import secrets
import time
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

CODE_TTL_S = 300
ACCESS_TTL_S = 3600
REFRESH_TTL_S = 30 * 24 * 3600


async def earth_login_ok(user: str, password: str) -> Any | None:
    """Проверка кредов единственным доступным способом — реальным логином в Earth.

    Возвращает готовый Earth-клиент для пользовательского кэша или None.
    Точка подмены в тестах (offline).
    """
    from logicore_mcp.config import load_settings
    from logicore_mcp.earth.client import HttpEarthClient
    from logicore_mcp.earth.scheduler import ScheduledEarthClient

    settings = load_settings().model_copy(update={"user": user, "password": password})
    client = ScheduledEarthClient(HttpEarthClient(settings), settings)
    await client.user_context()
    return client


class SimpleOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """Провайдер-в-памяти поверх протокола SDK: клиенты, коды, пара access/refresh."""

    def __init__(self) -> None:
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._access: dict[str, AccessToken] = {}
        self._refresh: dict[str, RefreshToken] = {}
        self.pending: dict[str, tuple[str, AuthorizationParams]] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        issue = secrets.token_urlsafe(24)
        self.pending[issue] = (client.client_id, params)
        return f"/login?issue={issue}"

    def issue_code(self, issue: str, username: str) -> str | None:
        """Завершение логина: код авторизации для отложенного запроса /authorize."""
        pending = self.pending.pop(issue, None)
        if pending is None:
            return None
        client_id, params = pending
        code = AuthorizationCode(
            code=secrets.token_urlsafe(32),
            scopes=params.scopes or [],
            expires_at=time.time() + CODE_TTL_S,
            client_id=client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=username,
        )
        self._codes[code.code] = code
        return construct_redirect_uri(str(params.redirect_uri), code=code.code, state=params.state)

    def _issue_pair(self, client_id: str, scopes: list[str], subject: str, resource: str | None) -> OAuthToken:
        now = time.time()
        access = AccessToken(
            token=secrets.token_urlsafe(32),
            client_id=client_id,
            scopes=scopes,
            expires_at=int(now + ACCESS_TTL_S),
            resource=resource,
            subject=subject,
        )
        refresh = RefreshToken(
            token=secrets.token_urlsafe(32),
            client_id=client_id,
            scopes=scopes,
            expires_at=int(now + REFRESH_TTL_S),
            subject=subject,
        )
        self._access[access.token] = access
        self._refresh[refresh.token] = refresh
        return OAuthToken(
            access_token=access.token,
            token_type="Bearer",
            expires_in=ACCESS_TTL_S,
            refresh_token=refresh.token,
            scope=" ".join(scopes),
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._codes.pop(authorization_code.code, None)
        return self._issue_pair(
            client.client_id, authorization_code.scopes, authorization_code.subject or "", authorization_code.resource
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        token = self._refresh.get(refresh_token)
        if token is None or token.client_id != client.client_id:
            return None
        return token

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        self._refresh.pop(refresh_token.token, None)
        return self._issue_pair(client.client_id, scopes, refresh_token.subject or "", None)

    async def load_access_token(self, token: str) -> AccessToken | None:
        access = self._access.get(token)
        if access is None or (access.expires_at is not None and access.expires_at < time.time()):
            return None
        return access

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            self._access.pop(token.token, None)
            paired_refresh = next((r for r in self._refresh.values() if r.subject == token.subject), None)
            if paired_refresh is not None:
                self._refresh.pop(paired_refresh.token, None)
        else:
            self._refresh.pop(token.token, None)
            paired_access = next((a for a in self._access.values() if a.subject == token.subject), None)
            if paired_access is not None:
                self._access.pop(paired_access.token, None)


PROVIDER: SimpleOAuthProvider | None = None

SERVICE_SUBJECT = "svc:token"
SERVICE_CLIENT_ID = "service"


class ServiceTokenVerifier:
    """Обёртка TokenVerifier: статический LOGIQ_API_TOKEN → сервисный субъект (ревизия ADR-0011).

    Автономные потребители (ADR-0009, golden, планировщик) не проходят браузерный
    логин: их bearer равен LOGIQ_API_TOKEN и опознаётся как субъект svc:token с
    сервисной Earth-сессией (fallback runtime). Остальные токены — провайдеру.
    """

    def __init__(self, inner: Any, service_token: str) -> None:
        self._inner = inner
        self._service_token = service_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if token == self._service_token:
            return AccessToken(
                token=token,
                client_id=SERVICE_CLIENT_ID,
                scopes=[],
                expires_at=None,
                resource=None,
                subject=SERVICE_SUBJECT,
            )
        return await self._inner.verify_token(token)


def build_auth(issuer_url: str) -> dict[str, Any]:
    """Аргументы MCPServer(...) для режима oauth: настройки + провайдер (ADR-0011)."""
    from pydantic import AnyHttpUrl

    global PROVIDER
    PROVIDER = SimpleOAuthProvider()
    issuer = AnyHttpUrl(issuer_url)
    return {
        "auth": AuthSettings(
            issuer_url=issuer,
            resource_server_url=issuer,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        ),
        "auth_server_provider": PROVIDER,
    }


def apply_oauth(mcp_server: Any, issuer_url: str) -> None:
    """Пост-конфигурация инстанса MCPServer в режиме oauth (ADR-0011).

    Единственная точка проводки и для продакшена (registry), и для тестов —
    чтобы пути не расходились. Приватные поля SDK — осознанно: конструкция
    сервера с auth на месте невозможна без цикла импортов (tools декораторами
    привязаны к глобальному инстансу).
    """
    from mcp.server.auth.provider import ProviderTokenVerifier

    from logicore_mcp.config import load_settings

    kwargs = build_auth(issuer_url)
    mcp_server.settings.auth = kwargs["auth"]
    mcp_server._auth_server_provider = kwargs["auth_server_provider"]
    verifier: Any = ProviderTokenVerifier(kwargs["auth_server_provider"])
    service_token = load_settings().api_token
    if service_token:
        verifier = ServiceTokenVerifier(verifier, service_token)
    mcp_server._token_verifier = verifier
