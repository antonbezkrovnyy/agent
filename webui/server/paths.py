from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "research"
FRONTEND_DIST = ROOT / "webui" / "frontend" / "dist"


def resolve_workspace_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError("Путь должен находиться внутри рабочей папки проекта")
    return resolved


def relative_path(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()
