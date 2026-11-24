import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from agent import make_agent, run_observation
import grok
import mapping
import normalization
import runtime
from schemas import FieldConstraint, ParserResult, RecordField
import storage


def matching_revisions(raw: str, revisions: list[dict]) -> list[dict]:
    matches = []
    for item in revisions:
        response = storage.get_family(item["family_id"], item["revision"])
        if not response["ok"]:
            continue
        family = response["family"]
        record_schema = [RecordField.model_validate(field) for field in family["record_schema"]]
        constraints = [FieldConstraint.model_validate(rule) for rule in family["constraints"]]
        parser_result = runtime.execute_family(family, raw)
        validation = grok.validate_result(
            ParserResult.model_validate(parser_result),
            record_schema,
            constraints,
        )
        if validation["ok"]:
            matches.append({
                "family_id": item["family_id"],
                "revision": item["revision"],
                "captures": len(parser_result["captures"]),
            })
    return matches


def matching_parsers(raw: str) -> list[dict]:
    latest = storage.list_families()["families"]
    matches = matching_revisions(raw, latest)
    if matches:
        return matches
    latest_ids = {(item["family_id"], item["revision"]) for item in latest}
    history = [
        item for item in storage.list_parser_revisions()
        if (item["family_id"], item["revision"]) not in latest_ids
    ]
    return matching_revisions(raw, history)


def append_progress(path: Path, event: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(),
            **event,
        }, ensure_ascii=False) + "\n")
        stream.flush()


def apply_normalization(agent, family_id: str, revision: int, raw: str) -> dict | None:
    if agent is None:
        return None
    saved = storage.get_normalization(family_id, parser_revision=revision)
    if not saved["ok"]:
        from normalization_agent import run_normalization
        run_normalization(agent, family_id, revision)
    result = normalization.normalize_with_latest(family_id, revision, raw)
    return {
        "status": result["status"],
        "normalization_revision": result.get("normalization_revision"),
        "errors": result["errors"],
    }


def apply_mapping(agent, family_id: str, revision: int, raw: str, source_timezone: str | None) -> dict | None:
    if agent is None:
        return None
    normalization_saved = storage.get_normalization(family_id, parser_revision=revision)
    if not normalization_saved["ok"]:
        return {"status": "mapping_unavailable", "errors": ["normalization_not_found"]}
    normalization_revision = normalization_saved["normalization"]["revision"]
    saved = storage.get_mapping(family_id, normalization_revision=normalization_revision)
    if not saved["ok"]:
        from mapping_agent import run_mapping
        run_mapping(agent, family_id, revision, normalization_revision, source_timezone)
    result = mapping.map_with_latest(family_id, revision, raw)
    return {
        "status": result["status"],
        "mapping_revision": result.get("mapping_revision"),
        "taxonomy": result.get("taxonomy"),
        "errors": result["errors"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Построчное покрытие RAW corpus сохранёнными parser families.")
    parser.add_argument("file", type=Path, help="UTF-8 файл с одной RAW-строкой на строку.")
    parser.add_argument("--reset", action="store_true", help="Удалить текущую RESEARCH_DB и журналы перед запуском.")
    parser.add_argument("--end-line", type=int, help="Обработать только строки с первой по указанную включительно.")
    parser.add_argument("--normalize", action="store_true", help="Создавать и применять normalization revisions после parser dispatch.")
    parser.add_argument("--map-taxonomy", choices=["mp_siem_8.0"], help="После normalization создавать и применять mapping в указанную таксономию.")
    parser.add_argument("--source-timezone", help="Внешняя timezone для naive timestamp при маппинге.")
    args = parser.parse_args()

    database = Path(os.getenv("RESEARCH_DB", "research/log_research.db"))
    progress_path = database.with_suffix(".progress.jsonl")
    summary_path = database.with_suffix(".summary.json")
    if args.reset:
        for path in (database, progress_path, summary_path):
            if path.exists():
                path.unlink()
        print(f"RESET database={database} built_in_grok=0", flush=True)

    lines = [line for line in args.file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.end_line is not None:
        if not 1 <= args.end_line <= len(lines):
            parser.error(f"--end-line должен быть в диапазоне 1..{len(lines)}")
        lines = lines[:args.end_line]
    total = len(lines)
    print(f"START total={total} database={database}", flush=True)
    prior_researched = 0
    if progress_path.exists():
        prior_researched = sum(
            json.loads(line).get("status") == "researched"
            for line in progress_path.read_text(encoding="utf-8").splitlines()
        )
    agent = make_agent()
    normalization_agent = None
    mapping_agent = None
    if args.map_taxonomy:
        args.normalize = True
    if args.normalize:
        from normalization_agent import make_normalization_agent
        normalization_agent = make_normalization_agent()
    if args.map_taxonomy:
        from mapping_agent import make_mapping_agent
        mapping_agent = make_mapping_agent()
    started = time.monotonic()
    covered = 0
    researched = 0

    for index, raw in enumerate(lines, 1):
        prefix = f"[{index}/{total}]"
        matches = matching_parsers(raw)
        if len({match["family_id"] for match in matches}) > 1:
            append_progress(progress_path, {
                "line": index,
                "status": "failed",
                "error": "ambiguous_family_match",
                "matches": matches,
                "raw": raw,
            })
            raise RuntimeError(f"ambiguous_family_match at line {index}: {matches}")
        if matches:
            covered += 1
            match = matches[0]
            print(
                f"{prefix} COVERED {match['family_id']}@{match['revision']} "
                f"matches={len(matches)} captures={match['captures']}",
                flush=True,
            )
            append_progress(progress_path, {
                "line": index,
                "status": "covered",
                "matches": matches,
                "raw": raw,
            })
            normalization_event = apply_normalization(
                normalization_agent, match["family_id"], match["revision"], raw
            )
            if normalization_event:
                print(f"{prefix} NORMALIZATION {normalization_event['status']}", flush=True)
                append_progress(progress_path, {"line": index, "status": "normalization", **normalization_event})
                mapping_event = apply_mapping(
                    mapping_agent, match["family_id"], match["revision"], raw, args.source_timezone
                )
                if mapping_event:
                    print(f"{prefix} MAPPING {mapping_event['status']}", flush=True)
                    append_progress(progress_path, {"line": index, "status": "mapping", **mapping_event})
            continue

        print(f"{prefix} UNCOVERED raw={raw[:180]!r}", flush=True)
        before = {
            (item["family_id"], item["revision"])
            for item in storage.list_parser_revisions()
        }
        try:
            response, research_id = run_observation(
                agent,
                raw,
                show_response=False,
                show_tools=True,
                compact_tools=True,
            )
        except Exception as error:
            append_progress(progress_path, {
                "line": index,
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
                "raw": raw,
            })
            print(f"{prefix} FAILED {type(error).__name__}: {error}", flush=True)
            raise
        researched += 1
        after = {
            (item["family_id"], item["revision"])
            for item in storage.list_parser_revisions()
        }
        new_revisions = sorted(after - before)
        print(
            f"{prefix} RESEARCHED id={research_id} new_revisions={new_revisions} "
            f"essence={response.essence[:220]!r}",
            flush=True,
        )
        append_progress(progress_path, {
            "line": index,
            "status": "researched",
            "research_id": research_id,
            "new_revisions": new_revisions,
            "raw": raw,
        })
        for family_id, revision in new_revisions:
            normalization_event = apply_normalization(
                normalization_agent, family_id, revision, raw
            )
            if normalization_event:
                print(f"{prefix} NORMALIZATION {normalization_event['status']}", flush=True)
                append_progress(progress_path, {"line": index, "status": "normalization", **normalization_event})
                mapping_event = apply_mapping(
                    mapping_agent, family_id, revision, raw, args.source_timezone
                )
                if mapping_event:
                    print(f"{prefix} MAPPING {mapping_event['status']}", flush=True)
                    append_progress(progress_path, {"line": index, "status": "mapping", **mapping_event})

    print("VERIFY full corpus against saved parser revisions", flush=True)
    uncovered = []
    for index, raw in enumerate(lines, 1):
        if not matching_parsers(raw):
            uncovered.append(index)
        if index % 100 == 0 or index == total:
            print(f"VERIFY {index}/{total} uncovered={len(uncovered)}", flush=True)
    if uncovered:
        raise RuntimeError(f"Corpus remains uncovered: {uncovered}")

    revisions = storage.list_parser_revisions()
    with storage.db_connect() as connection:
        grok_count = connection.execute("SELECT COUNT(*) FROM grok_patterns").fetchone()[0]
        research_count = connection.execute("SELECT COUNT(*) FROM research_results").fetchone()[0]
        normalization_count = connection.execute("SELECT COUNT(*) FROM normalization_revisions").fetchone()[0]
        mapping_count = connection.execute("SELECT COUNT(*) FROM mapping_revisions").fetchone()[0]
    summary = {
        "file": str(args.file),
        "database": str(database),
        "total_lines": total,
        "source_end_line": args.end_line or total,
        "covered_without_model": total - prior_researched - researched,
        "researched_with_model": prior_researched + researched,
        "uncovered_lines": uncovered,
        "families": len({item["family_id"] for item in revisions}),
        "parser_revisions": len(revisions),
        "grok_patterns": grok_count,
        "research_results": research_count,
        "normalization_revisions": normalization_count,
        "mapping_revisions": mapping_count,
        "duration_seconds": round(time.monotonic() - started, 3),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_progress(progress_path, {"status": "completed", "summary": summary})
    print(f"COMPLETE {json.dumps(summary, ensure_ascii=False)}", flush=True)


if __name__ == "__main__":
    main()
