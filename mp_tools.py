import json
from pathlib import Path
from typing import Annotated, Literal

from langchain_core.tools import tool
from pydantic import Field

import mp_native
from mp_export import compile_spec, export_package
from mp_profile import DEFAULT_PROFILE, load_profile
from mp_schemas import MpTextExportSpec
import storage
import tools as common_tools


def make_mp_text_tools(
    output_root: str | Path,
    mpsiem: str | Path = mp_native.DEFAULT_MPSIEM,
    native_mode: str = "docker",
    profile_path: str | Path = DEFAULT_PROFILE,
) -> list:
    output = Path(output_root)

    @tool
    def get_mp_text_capabilities() -> dict:
        """Возвращает точные TEXT tokens, VRL/MP functions и контракт Logicore выбранного runtime."""
        profile = load_profile(profile_path).data
        return {
            "ok": True,
            "name": profile["name"],
            "runtime": profile["runtime"],
            "capabilities": profile["capabilities"],
            "required_event_fields": profile["normalizer"]["required_event_fields"],
            "event_id_pattern": profile["output"]["event_id_pattern"],
            "forbidden_rule_fields": profile["taxonomy"]["forbidden_rule_fields"],
            "field_name_policy": "mapping target может быть dotted; YAML/VRL получает плоское имя через замену . на _",
            "capture_policy": "Все parser captures — temporary $variables; итоговые поля создаются только mappings.",
            "time_policy": "DATETIME capture либо parse_timestamp с явной source timezone; default_year compiler пока не понижает.",
        }

    @tool
    def preview_mp_text_candidate(spec: MpTextExportSpec) -> dict:
        """Компилирует candidate без записи и показывает YAML/CONF/fixtures либо точные IR errors."""
        return compile_spec(spec, load_profile(profile_path))

    @tool
    def evaluate_mp_text_candidate(spec: MpTextExportSpec) -> dict:
        """Strict-load и native-исполнение positive/negative fixtures выбранным mpsiem, без сохранения пакета."""
        compiled = compile_spec(spec, load_profile(profile_path))
        if not compiled["ok"]:
            return compiled
        native = mp_native.validate_rules_from_text(
            compiled["yaml"], compiled["fixtures"], mpsiem=mpsiem, mode=native_mode
        )
        return {
            "ok": bool(native.get("ok")),
            "native": native,
            "spec_hash": compiled["spec_hash"],
            "rules": compiled["rules"],
            "positive_fixtures": compiled["positive_fixtures"],
            "negative_fixtures": compiled["negative_fixtures"],
        }

    @tool
    def export_mp_normalizer(spec: MpTextExportSpec) -> dict:
        """Сохраняет YAML/CONF/fixtures/report только после успешной strict native проверки."""
        package = export_package(
            spec,
            output,
            Path(profile_path),
            native=True,
            mpsiem=mpsiem,
            native_mode=native_mode,
        )
        if not package.get("ok"):
            return package
        saved = storage.save_mp_export(spec.model_dump(mode="json"), package)
        if not saved["ok"]:
            return saved
        package["storage"] = saved
        Path(package["report_path"]).write_text(
            json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return package

    @tool
    def resolve_mp_feedback(
        feedback_id: Annotated[int, Field(ge=1)],
        resolution: Annotated[str, Field(min_length=1)],
        accepted_uncertainty: bool = False,
    ) -> dict:
        """Закрывает feedback после фактического исправления либо принятия внешней неопределённости."""
        return storage.resolve_feedback(
            feedback_id,
            resolution,
            "accepted_uncertainty" if accepted_uncertainty else "resolved",
        )

    target_tools = [
        get_mp_text_capabilities,
        preview_mp_text_candidate,
        evaluate_mp_text_candidate,
        export_mp_normalizer,
        resolve_mp_feedback,
    ]
    shared = [
        common_tools.report_feedback,
        common_tools.list_open_feedback,
        common_tools.get_feedback,
        common_tools.current_time,
        common_tools.duckduckgo_search,
        common_tools.get_mapping_guidelines,
        common_tools.semantic_search_taxonomy,
        common_tools.get_taxonomy_field,
        common_tools.get_taxonomy_values,
        common_tools.get_taxonomy_categories,
    ]
    return [*target_tools, *shared]
