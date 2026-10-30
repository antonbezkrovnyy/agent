"""Сборка и запуск: stdio (ADR-0002) или streamable-http (ADR-0010); stdout занят транспортом."""

from typing import Any

from logicore_mcp.mcp.registry import mcp
from logicore_mcp.mcp.runtime import schema_fields_catalog, settings

HEALTH_PATH = "/health"
MCP_HTTP_PATH = "/mcp"


@mcp.resource("logicore://schema/fields")
async def schema_fields() -> str:
    """Словарь полей событий (компактный список имён группами).
    Фильтровать search_events и выбирать field для top_values можно только по этим именам.
    Снимок фиксируется при первом чтении и живёт до перезапуска процесса MCP
    (метаданные стенда статичны); при ожидаемом дрейфе схемы — перезапустить сервер."""
    import json

    from logicore_mcp.domain.errors import LogicoreError
    from logicore_mcp.mcp.resources import render_fields_resource

    try:
        schema = await schema_fields_catalog()
    except LogicoreError as exc:
        return json.dumps({"ok": False, "error": str(exc), "hint": exc.hint}, ensure_ascii=False)
    return render_fields_resource(schema)


@mcp.custom_route(HEALTH_PATH, methods=["GET"])
async def health(request: Any) -> Any:
    """Liveness без авторизации — для healthcheck контейнера (ADR-0010)."""
    from starlette.responses import JSONResponse

    return JSONResponse({"ok": True, "transport": settings().transport})


LOGIN_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Логикор — вход</title></head>
<body style="font-family:system-ui;max-width:22rem;margin:4rem auto">
<h3>Вход в Логикор</h3>
<p>Введите учётные данные Earth — они проверяются реальным логином и нигде не сохраняются (ADR-0011).</p>
<form method="post" action="/login">
<input type="hidden" name="issue" value="{issue}">
<input name="username" placeholder="Логин Earth" autocomplete="username" required
       style="width:100%;padding:.5rem;margin:.3rem 0">
<input name="password" type="password" placeholder="Пароль" autocomplete="current-password" required
       style="width:100%;padding:.5rem;margin:.3rem 0">
<button style="padding:.5rem 1.5rem">Войти</button>
</form>{error}
</body></html>"""


@mcp.custom_route("/login", methods=["GET"])
async def login_page(request: Any) -> Any:
    """Страница логина oauth-флоу (ADR-0011): issue — отложенный запрос /authorize."""
    from starlette.responses import HTMLResponse

    issue = request.query_params.get("issue", "")
    error = "<p style='color:#b00'>Неверный логин или пароль</p>" if request.query_params.get("error") else ""
    return HTMLResponse(LOGIN_PAGE.format(issue=issue, error=error))


@mcp.custom_route("/login", methods=["POST"])
async def login_submit(request: Any) -> Any:
    """Проверка кредов реальным Earth-логином; успех — код авторизации (ADR-0011)."""
    from starlette.responses import HTMLResponse, RedirectResponse

    from logicore_mcp.mcp import oauth
    from logicore_mcp.mcp.runtime import register_user_client

    if oauth.PROVIDER is None:
        return HTMLResponse("oauth не включён (LOGIQ_AUTH)", status_code=500)
    form = await request.form()
    issue = str(form.get("issue", ""))
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))
    client = await oauth.earth_login_ok(username, password)
    if client is None:
        return RedirectResponse(f"/login?issue={issue}&error=1", status_code=302)
    register_user_client(username, client)
    redirect = oauth.PROVIDER.issue_code(issue, username)
    if redirect is None:
        return RedirectResponse("/login?error=expired", status_code=302)
    return RedirectResponse(redirect, status_code=302)


class BearerAuthMiddleware:
    """ASGI-middleware: статический bearer на каждый HTTP-запрос, кроме /health.

    Отказ — 401 + WWW-Authenticate: Bearer (ADR-0010). Слушаем только внутри
    доверенной сети; наружу эндпоинт не публикуется.
    """

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope.get("path") != HEALTH_PATH:
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])
            }
            if headers.get("authorization") != f"Bearer {self._token}":
                from starlette.responses import JSONResponse

                response = JSONResponse(
                    {"ok": False, "error": "Unauthorized", "hint": "Bearer-токен обязателен (ADR-0010)"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


async def _serve_http() -> None:
    import uvicorn

    config = settings()
    app: Any = mcp.streamable_http_app(streamable_http_path=MCP_HTTP_PATH, host=config.host)
    if config.auth == "token":
        app = BearerAuthMiddleware(app, config.api_token or "")
    server = uvicorn.Server(uvicorn.Config(app, host=config.host, port=config.port, log_level="info"))
    await server.serve()


def _register_tools() -> None:
    from logicore_mcp.mcp import prompts, tools  # noqa: F401 — регистрация декораторами (prompts — TD-0019)


def run() -> None:
    config = settings()
    if config.transport == "streamable-http":
        import asyncio

        _register_tools()
        asyncio.run(_serve_http())
        return
    _register_tools()
    mcp.run(transport="stdio")
