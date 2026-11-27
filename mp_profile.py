import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = 1
DEFAULT_PROFILE = Path(__file__).parent / "profiles" / "mp_text_profile_v1_1_7.json"
IDENTIFIER = re.compile(r"fn\s+identifier\s*\(\s*&self\s*\)\s*->\s*&'static\s+str\s*\{\s*\"([a-z0-9_]+)\"", re.DOTALL)
TEXT_TOKEN = re.compile(r'^\s*"([A-Z0-9_]+)"\s*=>\s*[^,]+,', re.MULTILINE)
COLUMN = re.compile(r"`([^`]+)`\s+(Nullable\((?:[^()]|\([^()]*\))*\)|DateTime\('UTC'\)|UUID|String)")


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class MpTextTargetProfile:
    data: dict[str, Any]

    @property
    def name(self) -> str:
        return self.data["name"]

    @property
    def text_tokens(self) -> frozenset[str]:
        return frozenset(self.data["capabilities"]["text_tokens"])

    @property
    def functions(self) -> frozenset[str]:
        capabilities = self.data["capabilities"]
        return frozenset(capabilities["mp_functions"] + capabilities["vrl_functions"])

    @property
    def storage_fields(self) -> dict[str, str]:
        return self.data["taxonomy"]["storage_fields"]

    @property
    def semantic_fields(self) -> frozenset[str]:
        return frozenset(self.data["taxonomy"]["semantic_fields"])

    def target_name(self, name: str) -> str:
        return name.replace(".", "_")

    def validate_target(self, name: str) -> str | None:
        flat_name = self.target_name(name)
        storage_name = self.data["taxonomy"]["event_field_aliases"].get(flat_name, flat_name)
        if storage_name not in self.storage_fields:
            return f"unknown_logicore_field:{name}"
        if flat_name in set(self.data["taxonomy"]["forbidden_rule_fields"]):
            return f"saturn_managed_field_forbidden:{name}"
        return None

    def public(self) -> dict[str, Any]:
        return self.data


def load_profile(path: str | Path = DEFAULT_PROFILE) -> MpTextTargetProfile:
    profile_path = Path(path)
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    required = {"schema_version", "name", "runtime", "capabilities", "taxonomy", "normalizer", "output"}
    missing = sorted(required - set(data))
    if missing:
        raise ProfileError(f"profile_missing_keys:{missing}")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ProfileError(f"unsupported_profile_schema:{data['schema_version']}")
    capabilities = data["capabilities"]
    if capabilities["grok_kinds"] != ["TEXT"]:
        raise ProfileError("mp_text_profile_must_allow_only_TEXT")
    if not capabilities["text_tokens"] or not capabilities["mp_functions"] or not capabilities["vrl_functions"]:
        raise ProfileError("empty_runtime_capabilities")
    return MpTextTargetProfile(data)


def _identifiers(root: Path) -> list[str]:
    names = set()
    for source in root.rglob("*.rs"):
        names.update(IDENTIFIER.findall(source.read_text(encoding="utf-8")))
    return sorted(names)


def _storage_fields(saturn_root: Path) -> dict[str, str]:
    schema_root = saturn_root / "migrator" / "db" / "migrations" / "schemas" / "mpsiem"
    fields = {}
    for migration in sorted(schema_root.glob("*.up.sql")):
        for name, field_type in COLUMN.findall(migration.read_text(encoding="utf-8")):
            name = name.strip()
            previous = fields.get(name)
            if previous is not None and previous != field_type:
                raise ProfileError(f"conflicting_storage_type:{name}:{previous}:{field_type}")
            fields[name] = field_type
    return dict(sorted(fields.items()))


def build_profile(mpsiem_root: Path, saturn_root: Path, vrl_stdlib_root: Path) -> dict[str, Any]:
    token_source = mpsiem_root / "src" / "grok" / "text" / "patterns_storage" / "mod.rs"
    tokens = sorted(set(TEXT_TOKEN.findall(token_source.read_text(encoding="utf-8"))))
    mp_functions = [
        name for name in _identifiers(mpsiem_root / "src" / "mpsiemlib")
        if name not in {"mp_submessage", "mp_table_search"}
    ]
    vrl_functions = _identifiers(vrl_stdlib_root)
    storage_fields = _storage_fields(saturn_root)

    from taxonomy import parse_reference

    reference = parse_reference()
    semantic_fields = sorted({field["name"] for field in reference["fields"]})
    flat_semantic_fields = {name.replace(".", "_") for name in semantic_fields}
    technical_fields = sorted(set(storage_fields) - flat_semantic_fields)
    forbidden = sorted({
        field["name"].replace(".", "_")
        for field in reference["fields"]
        if field["automatic"]
    } | {
        "message", "uuid", "recv_time", "generator", "normalized", "unparsed", "tags",
        "not_drop", "skip", "correlation_rule", "syslog_destination", "original_time",
    })

    if len(tokens) != 28:
        raise ProfileError(f"unexpected_v1_1_7_text_token_count:{len(tokens)}")
    if len(mp_functions) != 84:
        raise ProfileError(f"unexpected_v1_1_7_mp_function_count:{len(mp_functions)}")
    if len(vrl_functions) != 196:
        raise ProfileError(f"unexpected_vrl_0_27_function_count:{len(vrl_functions)}")

    return {
        "schema_version": SCHEMA_VERSION,
        "name": "logicore-mp-text-v1.1.7",
        "runtime": {
            "saturn_image": "nexus.inf.axel.pro/docker-group/logiq/saturn:v1.1.7",
            "saturn_revision": "98c36249d068b9ec375da307c628c16e802afd2a",
            "mpsiem_revision": "ffd58d2818ef850ac780f74c24d9a4e0b295c819",
            "vrl_version": "0.27.0",
        },
        "capabilities": {
            "grok_kinds": ["TEXT"],
            "text_tokens": tokens,
            "text_operators": ["?", "+", "*", "??", "+?", "*?", "groups", "alternatives"],
            "text_syntax": {
                "full_match": True,
                "capture": "{src.ip=IPV4} → src_ip; production target использует {$src_ip=IPV4}",
                "temporary_capture": "{$user=STRING}",
                "until": "{$user=UNTIL(\"]\")}",
                "datetime": "{$time=DATETIME_SYSLOG}",
                "optional_group": "{\"[\"$procid=NUMBER\"]\"}?",
                "alternatives": "{$time=DATETIME_SYSLOG|$time=DATETIME}",
                "literal": "\"Login OK:\" или просто Login OK:",
                "whitespace": "Пробел в pattern — серия MP-blank любой длины, включая нулевую.",
                "not_supported": ["%{WORD:user}", "$user:WORD", "$user=regex", "custom grok_patterns.txt"],
            },
            "mp_functions": mp_functions,
            "conditional_functions": {"subformulas": ["mp_submessage"]},
            "non_normalizer_functions": ["mp_table_search"],
            "vrl_functions": vrl_functions,
            "custom_pattern_registry": False,
        },
        "taxonomy": {
            "semantic_source": "MP SIEM 8.0 reference; Logicore subset plus technical fields",
            "semantic_fields": semantic_fields,
            "semantic_field_definitions": reference["fields"],
            "semantic_values": reference["values"],
            "semantic_categories": reference["categories"],
            "technical_fields": technical_fields,
            "storage_fields": storage_fields,
            "event_field_aliases": {"time": "timestamp"},
            "forbidden_rule_fields": forbidden,
            "default_storage_type": "String",
            "field_name_policy": "replace_dots_with_underscores",
        },
        "normalizer": {
            "body_field": "message",
            "mime_type_field": "mime",
            "mime": "TEXT",
            "pass_fields": {"recv_ipv4": "recv_ipv4", "recv_ipv6": "recv_ipv6"},
            "appendix": "{ null }\n",
            "subformulas": None,
            "required_event_fields": [
                "event_src_category", "event_src_title", "event_src_vendor",
                "action", "importance", "status", "object", "time", "id",
            ],
            "strict_load": True,
            "rule_selection": "unordered_first_matching",
            "source_routing": "specific_after_generic_with_source_success_precedence",
        },
        "output": {
            "rules_path": "pipeline/parse/{source}/norm_rules_{source}.yml",
            "conf_path": "pipeline/parse/autonormalizer/{source}.conf",
            "source_pattern": "^[a-z][a-z0-9_]*$",
            "rule_name": "{source}/normalization_formulas/{rule_key}",
            "event_id_pattern": "^PT_[A-Za-z0-9]+_[A-Za-z0-9]+_[A-Za-z0-9]+_[A-Za-z0-9_]+$",
            "uuid_policy": "uuid5_sha1_namespace_url_rule_key",
            "conf_success_tag": "{source}_normalized",
            "conf_failure_tag": "{source}_normalizer_failure",
            "conf_unparsed_tag": "unparsed_{source}",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and inspect versioned MP TEXT runtime profiles.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--mpsiem", type=Path, required=True)
    build.add_argument("--saturn", type=Path, required=True)
    build.add_argument("--vrl-stdlib", type=Path, required=True)
    build.add_argument("--output", type=Path, default=DEFAULT_PROFILE)
    show = subparsers.add_parser("show")
    show.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    args = parser.parse_args()

    if args.command == "build":
        data = build_profile(args.mpsiem, args.saturn, args.vrl_stdlib)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = {
            "ok": True,
            "output": str(args.output),
            "text_tokens": len(data["capabilities"]["text_tokens"]),
            "mp_functions": len(data["capabilities"]["mp_functions"]),
            "vrl_functions": len(data["capabilities"]["vrl_functions"]),
            "storage_fields": len(data["taxonomy"]["storage_fields"]),
        }
    else:
        profile = load_profile(args.profile)
        result = {
            "ok": True,
            "name": profile.name,
            "runtime": profile.data["runtime"],
            "text_tokens": len(profile.text_tokens),
            "functions": len(profile.functions),
            "storage_fields": len(profile.storage_fields),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
