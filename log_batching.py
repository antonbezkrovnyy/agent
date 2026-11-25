import ipaddress
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Literal
import warnings

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_distances

from schemas import SchemaModel


ParserAlgorithm = Literal["IPLoM", "Spell"]

_SEPARATOR_TRANSLATION = str.maketrans({character: " " for character in "/()[],:"})
_HEX_RE = re.compile(r"\b0[xX][0-9a-fA-F]+\b")
_IPV4_RE = re.compile(
    r"(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\w.])"
)
_IPV6_CANDIDATE_RE = re.compile(
    r"(?<![0-9a-fA-F:.])(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}(?![0-9a-fA-F:])"
)
_LOG_NAME = "batch.log"


class ClusterBatch(SchemaModel):
    cluster_id: str
    draft_template: str
    total_logs_in_cluster: int
    samples: list[str]


def normalize_for_clustering(raw: str) -> str:
    def mask_ipv6(match: re.Match[str]) -> str:
        candidate = match.group(0)
        try:
            ipaddress.IPv6Address(candidate)
        except ipaddress.AddressValueError:
            return candidate
        return "<IP>"

    normalized = _IPV4_RE.sub("<IP>", raw)
    normalized = _IPV6_CANDIDATE_RE.sub(mask_ipv6, normalized)
    normalized = _HEX_RE.sub("<HEX>", normalized)
    return " ".join(normalized.translate(_SEPARATOR_TRANSLATION).split())


def select_diverse_samples(raw_logs: list[str], sample_count: int = 5) -> list[str]:
    unique_logs = list(dict.fromkeys(raw_logs))
    if len(unique_logs) <= sample_count:
        return unique_logs

    matrix = TfidfVectorizer(token_pattern=r"\S+").fit_transform(unique_logs)
    distances = cosine_distances(matrix)
    selected = [0]
    is_selected = np.zeros(len(unique_logs), dtype=bool)
    is_selected[0] = True
    minimum_distances = distances[0].copy()

    while len(selected) < sample_count:
        candidates = np.where(is_selected, -np.inf, minimum_distances)
        next_index = int(np.argmax(candidates))
        selected.append(next_index)
        is_selected[next_index] = True
        minimum_distances = np.minimum(minimum_distances, distances[next_index])

    return [unique_logs[index] for index in selected]


def _make_parser(algorithm: ParserAlgorithm, directory: Path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        if algorithm == "IPLoM":
            from logparser.IPLoM import LogParser

            return LogParser(
                log_format="<Content>",
                indir=str(directory),
                outdir=str(directory),
                keep_para=False,
            )
        if algorithm == "Spell":
            from logparser.Spell import LogParser

            return LogParser(
                log_format="<Content>",
                indir=str(directory),
                outdir=str(directory),
                keep_para=False,
            )
    raise ValueError(f"Неподдерживаемый алгоритм: {algorithm!r}; ожидается IPLoM или Spell")


def _cluster_logs(raw_logs: list[str], algorithm: ParserAlgorithm) -> list[dict]:
    with TemporaryDirectory(prefix="log_batching_") as temporary_directory:
        directory = Path(temporary_directory)
        input_path = directory / _LOG_NAME
        normalized_logs = [normalize_for_clustering(raw) for raw in raw_logs]
        input_path.write_text("\n".join(normalized_logs) + "\n", encoding="utf-8")

        parser = _make_parser(algorithm, directory)
        parser.parse(_LOG_NAME)
        parsed = parser.df_log[["LineId", "EventId", "EventTemplate"]].to_dict("records")

    if len(parsed) != len(raw_logs):
        raise RuntimeError(
            f"{algorithm} пропустил строки: вход={len(raw_logs)}, результат={len(parsed)}"
        )

    clusters: dict[str, dict] = {}
    for expected_line_id, (raw, row) in enumerate(zip(raw_logs, parsed, strict=True), start=1):
        if int(row["LineId"]) != expected_line_id:
            raise RuntimeError(f"{algorithm} нарушил порядок строк на LineId={expected_line_id}")
        cluster_id = str(row["EventId"])
        if cluster_id not in clusters:
            clusters[cluster_id] = {
                "cluster_id": cluster_id,
                "draft_template": str(row["EventTemplate"]),
                "raw_logs": [],
            }
        clusters[cluster_id]["raw_logs"].append(raw)
    return list(clusters.values())


def prepare_log_batches(
    raw_logs: list[str],
    samples_per_cluster: int = 5,
    *,
    algorithm: ParserAlgorithm = "IPLoM",
) -> list[ClusterBatch]:
    if samples_per_cluster < 1:
        raise ValueError("samples_per_cluster должен быть >= 1")
    if algorithm not in ("IPLoM", "Spell"):
        raise ValueError(f"Неподдерживаемый алгоритм: {algorithm!r}; ожидается IPLoM или Spell")
    for index, raw in enumerate(raw_logs):
        if not raw.strip():
            raise ValueError(f"Пустая строка лога на позиции {index}")
        if "\n" in raw or "\r" in raw:
            raise ValueError(f"Многострочный лог на позиции {index}; ожидается одна строка")
    if not raw_logs:
        return []

    return [
        ClusterBatch(
            cluster_id=cluster["cluster_id"],
            draft_template=cluster["draft_template"],
            total_logs_in_cluster=len(cluster["raw_logs"]),
            samples=select_diverse_samples(cluster["raw_logs"], samples_per_cluster),
        )
        for cluster in _cluster_logs(raw_logs, algorithm)
    ]
