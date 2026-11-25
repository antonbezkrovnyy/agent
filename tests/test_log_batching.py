from pathlib import Path

import pytest

import log_batching
from log_batching import normalize_for_clustering, prepare_log_batches, select_diverse_samples


ASA_LOGS = [
    "<132>Feb 12 2023 13:00:30 10.11.200.67 : %ASA-4-106100: access-list CSM_FW_ACL_ denied tcp Outside/131.9.85.43(14015) -> PUB_DMZ/191.189.210.52(22) hit-cnt 1 first hit [0x6921d058, 0x00000000]",
    "<132>Feb 12 2023 13:00:31 10.11.200.67 : %ASA-4-106100: access-list CSM_FW_ACL_ denied tcp Outside/131.9.85.44(14016) -> PUB_DMZ/191.189.210.53(22) hit-cnt 2 first hit [0x6921d059, 0x00000000]",
    "<132>Feb 12 2023 13:00:32 10.11.200.67 : %ASA-4-106100: access-list CSM_FW_ACL_ denied udp Inside/10.1.2.3(53) -> PUB_DMZ/191.189.210.54(53) hit-cnt 3 first hit [0x6921d05a, 0x00000000]",
    "<132>Feb 12 2023 13:00:33 10.11.200.67 : %ASA-4-106100: access-list CSM_FW_ACL_ permitted tcp Outside/131.9.85.45(443) -> PUB_DMZ/191.189.210.55(443) hit-cnt 4 first hit [0x6921d05b, 0x00000000]",
    "<132>Feb 12 2023 13:00:34 10.11.200.67 : %ASA-4-106100: access-list CSM_FW_ACL_ denied tcp Outside/131.9.85.46(22) -> PUB_DMZ/191.189.210.56(22) hit-cnt 5 last hit [0x6921d05c, 0x00000000]",
    "<132>Feb 12 2023 13:00:35 10.11.200.67 : %ASA-4-106100: access-list CSM_FW_ACL_ denied icmp Outside/131.9.85.47(8) -> PUB_DMZ/191.189.210.57(0) hit-cnt 6 first hit [0x6921d05d, 0x00000000]",
]
SSH_LOGS = [
    "Feb 12 13:00:36 server sshd[123]: Accepted publickey for admin from 10.0.0.1 port 50122 ssh2: RSA SHA256:abc",
    "Feb 12 13:00:37 server sshd[124]: Accepted publickey for deploy from 10.0.0.2 port 50123 ssh2: ED25519 SHA256:def",
]
ASA_BUILT_LOG = "<132>Feb 12 2023 13:00:38 10.11.200.67 : %ASA-6-302013: Built inbound TCP connection 12345 for Outside/131.9.85.43(14015) to PUB_DMZ/191.189.210.52(22)"


def test_normalize_for_clustering_splits_and_masks_only_working_copy():
    normalized = normalize_for_clustering(ASA_LOGS[0])

    assert normalized == (
        "<132>Feb 12 2023 13 00 30 <IP> %ASA-4-106100 access-list CSM_FW_ACL_ "
        "denied tcp Outside <IP> 14015 -> PUB_DMZ <IP> 22 hit-cnt 1 first hit <HEX> <HEX>"
    )
    assert "131.9.85.43" not in normalized
    assert "0x6921d058" not in normalized
    assert normalize_for_clustering("host 2001:db8::1 port 443") == "host <IP> port 443"
    assert ASA_LOGS[0].startswith("<132>Feb 12 2023 13:00:30 10.11.200.67")


@pytest.mark.parametrize("algorithm", ["IPLoM", "Spell"])
def test_prepare_log_batches_groups_events_and_returns_original_contrastive_samples(algorithm):
    raw_logs = ASA_LOGS + [ASA_LOGS[0]] + SSH_LOGS + [ASA_BUILT_LOG]

    batches = prepare_log_batches(raw_logs, samples_per_cluster=4, algorithm=algorithm)

    assert len(batches) == 3
    asa_batch = next(batch for batch in batches if "%ASA-4-106100" in batch.samples[0])
    assert asa_batch.total_logs_in_cluster == len(ASA_LOGS) + 1
    assert len(asa_batch.samples) == 4
    assert len(set(asa_batch.samples)) == 4
    assert set(asa_batch.samples) <= set(ASA_LOGS)
    assert asa_batch.samples[0] == ASA_LOGS[0]
    assert any(" denied udp " in sample for sample in asa_batch.samples)
    assert any(" permitted tcp " in sample for sample in asa_batch.samples)
    assert all("<IP>" not in sample and "<HEX>" not in sample for sample in asa_batch.samples)

    ssh_batch = next(batch for batch in batches if "Accepted publickey" in batch.samples[0])
    assert ssh_batch.total_logs_in_cluster == 2
    assert ssh_batch.samples == SSH_LOGS
    assert sum(batch.total_logs_in_cluster for batch in batches) == len(raw_logs)


def test_farthest_point_sampling_is_deterministic_and_deduplicates():
    logs = [
        "service alpha started normally",
        "service alpha started normally",
        "service alpha started slowly",
        "kernel panic fatal memory corruption",
        "network interface link down",
    ]

    selected = select_diverse_samples(logs, sample_count=3)

    assert len(selected) == 3
    assert len(set(selected)) == 3
    assert selected[0] == logs[0]
    assert "kernel panic fatal memory corruption" in selected
    assert selected == select_diverse_samples(logs, sample_count=3)


def test_small_cluster_returns_all_unique_originals_without_vectorization(monkeypatch):
    def fail_vectorization(*args, **kwargs):
        raise AssertionError("TF-IDF не нужен для малого кластера")

    monkeypatch.setattr(log_batching.TfidfVectorizer, "fit_transform", fail_vectorization)

    assert select_diverse_samples(["first", "first", "second"], 5) == ["first", "second"]


def test_temporary_parser_files_are_removed(monkeypatch):
    created_paths = []
    original_temporary_directory = log_batching.TemporaryDirectory

    def tracked_temporary_directory(*args, **kwargs):
        directory = original_temporary_directory(*args, **kwargs)
        created_paths.append(Path(directory.name))
        return directory

    monkeypatch.setattr(log_batching, "TemporaryDirectory", tracked_temporary_directory)

    prepare_log_batches([ASA_LOGS[0]], algorithm="IPLoM")

    assert len(created_paths) == 1
    assert not created_paths[0].exists()


def test_empty_input_and_invalid_arguments():
    assert prepare_log_batches([]) == []

    with pytest.raises(ValueError, match="samples_per_cluster"):
        prepare_log_batches([ASA_LOGS[0]], samples_per_cluster=0)
    with pytest.raises(ValueError, match="алгоритм"):
        prepare_log_batches([ASA_LOGS[0]], algorithm="Drain")
    with pytest.raises(ValueError, match="Пустая строка"):
        prepare_log_batches([""])
    with pytest.raises(ValueError, match="Многострочный"):
        prepare_log_batches(["first\nsecond"])
