import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

from agent import make_batch_agent, run_research_batch
from corpus import append_progress, apply_mapping, apply_normalization, matching_parsers
from log_batching_drain3 import IndexedClusterBatch, prepare_indexed_log_batches
from schemas import LogSample, ResearchBatch


@dataclass(frozen=True)
class SourceLog:
    line: int
    raw: str


def matching_parser(raw: str) -> dict | None:
    matches = matching_parsers(raw)
    if len({match["family_id"] for match in matches}) > 1:
        raise RuntimeError(f"ambiguous_family_match: {matches}; raw={raw!r}")
    return matches[0] if matches else None


def make_research_batch(
    indexed_batch: IndexedClusterBatch,
    source_logs: list[SourceLog],
) -> ResearchBatch:
    samples = []
    for cluster_index in indexed_batch.sample_indices:
        source_index = indexed_batch.member_indices[cluster_index]
        source = source_logs[source_index]
        samples.append(LogSample(sample_id=f"line:{source.line}", raw=source.raw))
    return ResearchBatch(
        cluster_id=indexed_batch.cluster_id,
        total_logs_in_cluster=indexed_batch.total_logs_in_cluster,
        samples=samples,
    )


def batch_fingerprint(indexed_batch: IndexedClusterBatch, source_logs: list[SourceLog]) -> str:
    member_raws = [source_logs[index].raw for index in indexed_batch.member_indices]
    serialized = json.dumps(member_raws, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def completed_batch_fingerprints(progress_path: Path) -> set[str]:
    if not progress_path.exists():
        return set()
    return {
        event["fingerprint"]
        for line in progress_path.read_text(encoding="utf-8").splitlines()
        if (event := json.loads(line)).get("status") == "batch_researched"
        and event.get("fingerprint")
    }


def apply_downstream(
    normalization_agent,
    mapping_agent,
    match: dict,
    source: SourceLog,
    source_default_year: int | None,
    source_timezone: str | None,
    progress_path: Path,
) -> None:
    normalization_event = apply_normalization(
        normalization_agent,
        match["family_id"],
        match["revision"],
        source.raw,
        source_default_year,
    )
    if normalization_event:
        append_progress(progress_path, {
            "line": source.line,
            "status": "normalization",
            **normalization_event,
        })
        mapping_event = apply_mapping(
            mapping_agent,
            match["family_id"],
            match["revision"],
            source.raw,
            source_timezone,
        )
        if mapping_event:
            append_progress(progress_path, {
                "line": source.line,
                "status": "mapping",
                **mapping_event,
            })


def main() -> None:
    parser = argparse.ArgumentParser(description="Пакетное исследование непокрытых RAW через Drain3 и batch agent.")
    parser.add_argument("file", type=Path, help="UTF-8 файл с одной RAW-строкой на строку.")
    parser.add_argument("--samples-per-cluster", type=int, default=5)
    parser.add_argument("--similarity-threshold", type=float, default=0.4)
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--start-line", type=int)
    parser.add_argument("--end-line", type=int)
    parser.add_argument("--normalize", action="store_true")
    parser.add_argument("--map-taxonomy", choices=["mp_siem_8.0"])
    parser.add_argument("--source-timezone")
    parser.add_argument("--source-default-year", type=int)
    args = parser.parse_args()

    database = Path(os.getenv("RESEARCH_DB", "research/log_research.db"))
    progress_path = database.with_suffix(".batch-progress.jsonl")
    summary_path = database.with_suffix(".batch-summary.json")
    source_lines = [line for line in args.file.read_text(encoding="utf-8").splitlines() if line.strip()]
    source_total = len(source_lines)
    start_line = args.start_line or 1
    end_line = args.end_line or source_total
    if not 1 <= start_line <= end_line <= source_total:
        parser.error(f"требуется 1 <= start-line <= end-line <= {source_total}")
    lines = [
        SourceLog(line=index, raw=source_lines[index - 1])
        for index in range(start_line, end_line + 1)
    ]

    agent = make_batch_agent()
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
    initially_covered = 0
    uncovered = []
    for source in lines:
        if matching_parser(source.raw):
            initially_covered += 1
        else:
            uncovered.append(source)
    print(
        f"START total={len(lines)} covered={initially_covered} uncovered={len(uncovered)}",
        flush=True,
    )

    indexed_batches = prepare_indexed_log_batches(
        [source.raw for source in uncovered],
        samples_per_cluster=args.samples_per_cluster,
        similarity_threshold=args.similarity_threshold,
        depth=args.depth,
    )
    completed = completed_batch_fingerprints(progress_path)
    researched_batches = 0
    researched_groups = 0
    skipped_batches = 0

    for indexed_batch in indexed_batches:
        fingerprint = batch_fingerprint(indexed_batch, uncovered)
        member_sources = [uncovered[index] for index in indexed_batch.member_indices]
        if fingerprint in completed:
            skipped_batches += 1
            continue
        if all(matching_parser(source.raw) for source in member_sources):
            skipped_batches += 1
            append_progress(progress_path, {
                "status": "batch_covered",
                "fingerprint": fingerprint,
                "cluster_id": indexed_batch.cluster_id,
                "lines": [source.line for source in member_sources],
            })
            continue

        batch = make_research_batch(indexed_batch, uncovered)
        print(
            f"RESEARCH cluster={batch.cluster_id} total={batch.total_logs_in_cluster} "
            f"samples={len(batch.samples)}",
            flush=True,
        )
        try:
            response, research_ids = run_research_batch(
                agent,
                batch,
                show_response=False,
                show_tools=True,
                compact_tools=True,
            )
        except Exception as error:
            append_progress(progress_path, {
                "status": "batch_failed",
                "fingerprint": fingerprint,
                "cluster_id": batch.cluster_id,
                "lines": [source.line for source in member_sources],
                "error": f"{type(error).__name__}: {error}",
            })
            raise
        researched_batches += 1
        researched_groups += len(response.groups)
        append_progress(progress_path, {
            "status": "batch_researched",
            "fingerprint": fingerprint,
            "cluster_id": batch.cluster_id,
            "lines": [source.line for source in member_sources],
            "sample_ids": [sample.sample_id for sample in batch.samples],
            "research_ids": research_ids,
            "unresolved_sample_ids": response.unresolved_sample_ids,
        })

    uncovered_lines = []
    for source in lines:
        match = matching_parser(source.raw)
        if not match:
            uncovered_lines.append(source.line)
            continue
        append_progress(progress_path, {
            "line": source.line,
            "status": "covered",
            "family_id": match["family_id"],
            "revision": match["revision"],
        })
        apply_downstream(
            normalization_agent,
            mapping_agent,
            match,
            source,
            args.source_default_year,
            args.source_timezone,
            progress_path,
        )

    summary = {
        "file": str(args.file),
        "database": str(database),
        "total_lines": len(lines),
        "initially_covered": initially_covered,
        "input_uncovered": len(uncovered),
        "batches": len(indexed_batches),
        "researched_batches": researched_batches,
        "researched_groups": researched_groups,
        "skipped_batches": skipped_batches,
        "uncovered_lines": uncovered_lines,
        "duration_seconds": round(time.monotonic() - started, 3),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_progress(progress_path, {"status": "completed", "summary": summary})
    print(f"COMPLETE {json.dumps(summary, ensure_ascii=False)}", flush=True)
    if uncovered_lines:
        raise RuntimeError(f"Corpus remains uncovered: {uncovered_lines}")


if __name__ == "__main__":
    main()
