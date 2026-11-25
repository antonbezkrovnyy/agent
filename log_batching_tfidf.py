import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_distances

from schemas import SchemaModel


class ClusterBatch(SchemaModel):
    cluster_id: int
    total_logs_in_cluster: int
    samples: list[str]


def _clustering_vectors(raw_logs: list[str]):
    min_df = 2 if len(raw_logs) >= 10 else 1
    vectorizer = TfidfVectorizer(
        token_pattern=r"[a-zA-Z_%-][a-zA-Z0-9_%-]*",
        min_df=min_df,
    )
    try:
        return vectorizer.fit_transform(raw_logs)
    except ValueError as error:
        if min_df != 2 or "empty vocabulary" not in str(error):
            raise
        return TfidfVectorizer(
            token_pattern=r"[a-zA-Z_%-][a-zA-Z0-9_%-]*",
            min_df=1,
        ).fit_transform(raw_logs)


def select_diverse_sample_indices(raw_logs: list[str], sample_count: int) -> list[int]:
    unique_indices = []
    seen = set()
    for index, raw in enumerate(raw_logs):
        if raw not in seen:
            seen.add(raw)
            unique_indices.append(index)
    if len(unique_indices) <= sample_count:
        return unique_indices

    unique_logs = [raw_logs[index] for index in unique_indices]
    vectors = TfidfVectorizer(
        token_pattern=r"\S+",
        min_df=1,
    ).fit_transform(unique_logs)
    distances = cosine_distances(vectors)
    selected = [0]
    while len(selected) < sample_count:
        minimum_distances = distances[:, selected].min(axis=1)
        minimum_distances[selected] = -np.inf
        selected.append(int(np.argmax(minimum_distances)))
    return [unique_indices[index] for index in selected]


def select_diverse_samples(raw_logs: list[str], sample_count: int) -> list[str]:
    return [
        raw_logs[index]
        for index in select_diverse_sample_indices(raw_logs, sample_count)
    ]


def prepare_log_batches(
    raw_logs: list[str],
    samples_per_cluster: int = 5,
    distance_threshold: float = 0.35,
) -> list[ClusterBatch]:
    if samples_per_cluster < 1:
        raise ValueError("samples_per_cluster должен быть >= 1")
    if distance_threshold < 0:
        raise ValueError("distance_threshold должен быть >= 0")
    if not raw_logs:
        return []
    if any(not raw.strip() for raw in raw_logs):
        raise ValueError("raw_logs не должен содержать пустые строки")
    if len(raw_logs) == 1:
        return [ClusterBatch(cluster_id=0, total_logs_in_cluster=1, samples=raw_logs)]

    distances = cosine_distances(_clustering_vectors(raw_logs))
    labels = AgglomerativeClustering(
        n_clusters=None,
        metric="precomputed",
        linkage="average",
        distance_threshold=distance_threshold,
    ).fit_predict(distances)

    clustered_logs: dict[int, list[str]] = {}
    for raw, label in zip(raw_logs, labels, strict=True):
        clustered_logs.setdefault(int(label), []).append(raw)

    batches = [
        ClusterBatch(
            cluster_id=cluster_id,
            total_logs_in_cluster=len(cluster_logs),
            samples=select_diverse_samples(cluster_logs, samples_per_cluster),
        )
        for cluster_id, cluster_logs in clustered_logs.items()
    ]
    return sorted(
        batches,
        key=lambda batch: batch.total_logs_in_cluster,
        reverse=True,
    )
