"""Доменные ошибки. В конверт {"ok": false, "error", "hint"} превращает mcp-слой (TD-0001)."""


class LogicoreError(Exception):
    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class WindowError(LogicoreError):
    pass


class FilterError(LogicoreError):
    pass


class IqlError(LogicoreError):
    pass


class EarthError(LogicoreError):
    pass
