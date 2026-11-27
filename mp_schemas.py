from typing import Annotated

from pydantic import Field

from schemas import MappingField, NormalizationField, RecordField, SchemaModel


class MpTextRule(SchemaModel):
    rule_key: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    description: Annotated[str, Field(min_length=1)]
    pattern: Annotated[str, Field(min_length=1)]
    condition: str | None = None
    record_schema: list[RecordField]
    normalization: list[NormalizationField]
    mappings: list[MappingField]
    evidence: Annotated[list[str], Field(min_length=1)]
    expected_events: Annotated[list[dict], Field(min_length=1)]
    native_expected_events: list[dict] | None = None
    negative_evidence: list[str] = []


class MpTextExportSpec(SchemaModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    source: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    revision: int = Field(default=1, ge=1)
    dispatch_contains: Annotated[list[str], Field(min_length=1)]
    source_timezone: str | None = None
    rules: Annotated[list[MpTextRule], Field(min_length=1)]
    assumptions: list[str] = []


class MpTextCandidateResponse(SchemaModel):
    source: str
    revision: int
    yaml_path: str
    conf_path: str
    fixtures_path: str
    report_path: str
    native_ok: bool
    rules: int
    positive_fixtures: int
    negative_fixtures: int
    assumptions: list[str]
