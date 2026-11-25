from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

from log_batching_tfidf import ClusterBatch, select_diverse_sample_indices


class IndexedClusterBatch(ClusterBatch):
    member_indices: list[int]
    sample_indices: list[int]


def _validate_arguments(
    raw_logs: list[str],
    samples_per_cluster: int,
    similarity_threshold: float,
    depth: int,
) -> None:
    if samples_per_cluster < 1:
        raise ValueError("samples_per_cluster должен быть >= 1")
    if not 0 <= similarity_threshold <= 1:
        raise ValueError("similarity_threshold должен быть в диапазоне [0, 1]")
    if depth < 3:
        raise ValueError("depth должен быть >= 3")
    if any(not raw.strip() for raw in raw_logs):
        raise ValueError("raw_logs не должен содержать пустые строки")


def prepare_indexed_log_batches(
    raw_logs: list[str],
    samples_per_cluster: int = 5,
    similarity_threshold: float = 0.4,
    depth: int = 4,
) -> list[IndexedClusterBatch]:
    _validate_arguments(raw_logs, samples_per_cluster, similarity_threshold, depth)
    if not raw_logs:
        return []

    config = TemplateMinerConfig()
    config.drain_sim_th = similarity_threshold
    config.drain_depth = depth
    miner = TemplateMiner(persistence_handler=None, config=config)

    clustered_logs: dict[int, list[str]] = {}
    clustered_indices: dict[int, list[int]] = {}
    for index, raw in enumerate(raw_logs):
        result = miner.add_log_message(raw)
        cluster_id = int(result["cluster_id"])
        clustered_logs.setdefault(cluster_id, []).append(raw)
        clustered_indices.setdefault(cluster_id, []).append(index)

    batches = []
    for cluster_id, cluster_logs in clustered_logs.items():
        sample_indices = select_diverse_sample_indices(cluster_logs, samples_per_cluster)
        batches.append(IndexedClusterBatch(
            cluster_id=cluster_id,
            total_logs_in_cluster=len(cluster_logs),
            samples=[cluster_logs[index] for index in sample_indices],
            member_indices=clustered_indices[cluster_id],
            sample_indices=sample_indices,
        ))
    return sorted(
        batches,
        key=lambda batch: batch.total_logs_in_cluster,
        reverse=True,
    )


def prepare_log_batches(
    raw_logs: list[str],
    samples_per_cluster: int = 5,
    similarity_threshold: float = 0.4,
    depth: int = 4,
) -> list[ClusterBatch]:
    return [
        ClusterBatch(
            cluster_id=batch.cluster_id,
            total_logs_in_cluster=batch.total_logs_in_cluster,
            samples=batch.samples,
        )
        for batch in prepare_indexed_log_batches(
            raw_logs,
            samples_per_cluster,
            similarity_threshold,
            depth,
        )
    ]
