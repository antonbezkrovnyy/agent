import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

module_path = Path(__file__).resolve().parents[1] / "analysis-service" / "app.py"
spec = importlib.util.spec_from_file_location("analysis_service_app", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
app = module.app


client = TestClient(app)
ROWS = [
    {"uuid": "1", "src_ip": "10.0.0.1", "dst_ip": "10.0.0.2", "original_time": "2023-07-23", "action": "deny"},
    {"uuid": "2", "src_ip": "10.0.0.1", "dst_ip": "10.0.0.2", "original_time": "2026-11-06", "action": "deny"},
    {"uuid": "3", "src_ip": "10.0.0.3", "dst_ip": "10.0.0.4", "original_time": None, "action": "deny"},
]


def analyze(query):
    response = client.post("/analyze", json={
        "dataset_id": "dataset-test",
        "rows": ROWS,
        "query": query,
        "max_rows": 100,
    })
    assert response.status_code == 200
    return response.json()


def test_sql_computes_exact_composite_distinct_and_filtered_counts():
    result = analyze("""
        SELECT
            COUNT(*) AS total,
            COUNT(DISTINCT (src_ip, dst_ip)) AS pairs,
            COUNT(*) FILTER (WHERE original_time < '2026-01-01') AS archived,
            COUNT(*) FILTER (WHERE original_time IS NULL) AS missing_time
        FROM events
    """)

    assert result["ok"] is True
    assert result["rows"] == [{"total": 3, "pairs": 2, "archived": 1, "missing_time": 1}]
    assert result["truncated"] is False


def test_sql_result_limit_is_explicit():
    result = client.post("/analyze", json={
        "dataset_id": "dataset-test",
        "rows": ROWS,
        "query": "SELECT * FROM events ORDER BY uuid",
        "max_rows": 2,
    }).json()

    assert result["returned_rows"] == 2
    assert result["truncated"] is True


def test_sql_rejects_writes_multiple_statements_and_external_reads():
    for query in (
        "DELETE FROM events",
        "SELECT * FROM events; SELECT 1",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_json_auto('/tmp/other.jsonl')",
    ):
        result = analyze(query)
        assert result["ok"] is False
        assert result["error_code"] == "invalid_query"


def test_declared_columns_exist_even_when_all_values_are_missing():
    result = client.post("/analyze", json={
        "dataset_id": "dataset-missing",
        "rows": [{"uuid": "1", "timestamp": "2026-11-06T00:00:00Z"}],
        "columns": ["uuid", "timestamp", "src_ip", "dst_ip"],
        "query": "SELECT COUNT(DISTINCT (src_ip, dst_ip)) AS pairs FROM events",
        "max_rows": 10,
    }).json()

    assert result["ok"] is True
    assert result["rows"] == [{"pairs": 1}]


def test_empty_dataset_keeps_declared_schema():
    result = client.post("/analyze", json={
        "dataset_id": "dataset-empty-schema",
        "rows": [],
        "columns": ["src_ip", "dst_ip"],
        "query": "SELECT COUNT(*) AS total, COUNT(DISTINCT (src_ip, dst_ip)) AS pairs FROM events",
        "max_rows": 10,
    }).json()

    assert result["ok"] is True
    assert result["rows"] == [{"total": 0, "pairs": 0}]


def test_missing_column_has_machine_readable_recovery():
    result = client.post("/analyze", json={
        "dataset_id": "dataset-missing-recovery",
        "rows": [{"uuid": "1", "recv_ipv4": "10.0.0.1"}],
        "columns": ["uuid", "recv_ipv4"],
        "query": "SELECT src_ip FROM events",
        "max_rows": 10,
    }).json()

    assert result["ok"] is False
    assert result["error_code"] == "missing_column"
    assert result["missing_columns"] == ["src_ip"]
    assert result["available_columns"] == ["recv_ipv4", "uuid"]
    assert "не повторяйте" in result["hint"]


def test_double_quoted_literal_has_sql_specific_recovery():
    result = client.post("/analyze", json={
        "dataset_id": "dataset-literal-recovery",
        "rows": [{"action": None}],
        "columns": ["action"],
        "query": "SELECT * FROM events WHERE action = 'deny'",
        "max_rows": 10,
    }).json()

    assert result["ok"] is False
    assert result["error_code"] == "invalid_literal"
    assert "action = 'deny'" in result["hint"]
