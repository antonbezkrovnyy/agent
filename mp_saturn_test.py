import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

import yaml


class SaturnTestError(RuntimeError):
    pass


def run_command(command: list[str], cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def normalize_lf(path: Path) -> None:
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))


def clear_working_tree(path: Path) -> None:
    for child in path.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def copy_working_tree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(".git"),
        dirs_exist_ok=True,
    )


@contextmanager
def substituted_pipelines(tools_root: Path, pipelines: Path, package_root: Path):
    destination = tools_root / "docker" / "saturn-pipelines"
    entrypoint = tools_root / "docker" / "saturn" / "entrypoint.sh"
    if not destination.is_dir() or not pipelines.is_dir():
        raise SaturnTestError("saturn-tools/docker/saturn-pipelines или исходный pipelines не найден")
    with tempfile.TemporaryDirectory(prefix="mp-saturn-pipelines-backup-") as temporary:
        backup = Path(temporary) / "saturn-pipelines"
        backup.mkdir()
        copy_working_tree(destination, backup)
        original_entrypoint = entrypoint.read_bytes()
        try:
            clear_working_tree(destination)
            copy_working_tree(pipelines, destination)
            package_pipeline = package_root / "pipeline"
            if package_pipeline.is_dir():
                copy_working_tree(package_pipeline, destination / "pipeline")
            else:
                raise SaturnTestError(f"В package root нет pipeline/: {package_root}")
            normalize_lf(entrypoint)
            yield destination
        finally:
            clear_working_tree(destination)
            copy_working_tree(backup, destination)
            entrypoint.write_bytes(original_entrypoint)


def config_test(tools_root: Path, timeout: int = 300) -> dict:
    command = [
        "docker", "compose", "-f", "docker-compose.yml", "run", "--rm", "--no-deps",
        "--entrypoint", "/usr/local/bin/docker-entrypoint",
        "-e", "LS_JAVA_OPTS=-Xms256m -Xmx2g -Xss2m",
        "-e", "CLICKHOUSE_URLS=[[\"http://clickhouse:8123\"]]",
        "saturn", "/usr/share/logstash/bin/logstash", "--config.test_and_exit",
    ]
    try:
        completed = run_command(command, tools_root / "docker", timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "error": "config_test_start_failed", "detail": str(error)}
    return {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-10000:],
        "stderr": completed.stderr[-10000:],
    }


def ensure_no_fixed_containers() -> None:
    names = [
        "mp-saturn-agent", "mp-saturn-agent-migrator",
        "mp-saturn-agent-clickhouse", "mp-saturn-agent-rabbitmq",
    ]
    completed = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30
    )
    if completed.returncode != 0:
        raise SaturnTestError(completed.stderr)
    existing = sorted(set(names) & set(completed.stdout.splitlines()))
    if existing:
        raise SaturnTestError(f"Фиксированные container names уже существуют, стенд не трогаем: {existing}")


def start_stack(tools_root: Path, timeout: int = 300) -> dict:
    with tempfile.TemporaryDirectory(prefix="mp-saturn-compose-") as temporary:
        override = Path(temporary) / "memory.yml"
        override.write_text(yaml.safe_dump({
            "volumes": {"clickhouse-storage": {"name": "mp-saturn-agent-clickhouse-storage"}},
            "services": {
                "clickhouse": {
                    "container_name": "mp-saturn-agent-clickhouse",
                    "ports": ["19000:9000", "18123:8123", "19009:9009"],
                },
                "rabbitmq": {
                    "container_name": "mp-saturn-agent-rabbitmq",
                    "ports": ["15673:15672"],
                },
                "saturn-migrator": {"container_name": "mp-saturn-agent-migrator"},
                "saturn": {
                    "container_name": "mp-saturn-agent",
                    "environment": {"LS_JAVA_OPTS": "-Xms256m -Xmx2g -Xss2m"},
                    "ports": [
                        "19600:9600", "15140:5140/tcp", "15140:5140/udp",
                        "15142:5142/tcp", "15142:5142/udp",
                        "16140:6140/tcp", "16140:6140/udp",
                    ],
                },
            }
        }), encoding="utf-8")
        command = [
            "docker", "compose", "--project-name", "mp-saturn-agent",
            "-f", "docker-compose.yml", "-f", str(override), "up", "-d",
            "clickhouse", "rabbitmq", "saturn-migrator", "saturn",
        ]
        reset_command = [
            "docker", "compose", "--project-name", "mp-saturn-agent",
            "-f", "docker-compose.yml", "-f", str(override), "down", "--volumes", "--remove-orphans",
        ]
        try:
            reset = subprocess.run(
                reset_command, cwd=tools_root / "docker", capture_output=True, text=True, timeout=120,
            )
            if reset.returncode != 0:
                return {
                    "ok": False,
                    "error": "isolated_stack_reset_failed",
                    "stderr": reset.stderr[-4000:],
                }
            completed = subprocess.run(
                command, cwd=tools_root / "docker", capture_output=True, text=True, timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"ok": False, "error": "stack_start_failed", "detail": str(error)}
    return {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-10000:],
        "stderr": completed.stderr[-10000:],
    }


def stop_stack(tools_root: Path) -> None:
    subprocess.run(
        ["docker", "stop", "mp-saturn-agent", "mp-saturn-agent-migrator",
         "mp-saturn-agent-rabbitmq", "mp-saturn-agent-clickhouse"],
        capture_output=True, text=True, timeout=120,
    )


def wait_for_pipelines(timeout: int = 120) -> None:
    required = {
        "input_syslog", "input_wlb", "parse_autonormalizer", "client_settings",
        "mpsiem_tags", "clonning", "output_siem", "parse_windows",
    }
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:19600/_node/stats/pipelines", timeout=5
            ) as response:
                data = json.loads(response.read().decode())
            pipelines = set(data.get("pipelines", {}))
            if required <= pipelines:
                return
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise SaturnTestError("Logstash pipelines не стали доступны за отведённое время")


def send_tcp(raw_logs: list[str], host: str = "127.0.0.1", port: int = 15140) -> None:
    with socket.create_connection((host, port), timeout=10) as connection:
        for raw in raw_logs:
            connection.sendall(raw.encode("utf-8") + b"\n")


def clickhouse_query(tools_root: Path, query: str) -> list[dict]:
    config = yaml.safe_load((tools_root / "docker" / "saturn" / "config.yml").read_text(encoding="utf-8"))
    credentials = config["clickhouse"]
    parameters = urllib.parse.urlencode({
        "query": query + " FORMAT JSONEachRow SETTINGS output_format_json_quote_64bit_integers=0"
    })
    request = urllib.request.Request(
        "http://127.0.0.1:18123/?" + parameters,
        method="POST",
    )
    import base64
    token = base64.b64encode(
        f"{credentials['username']}:{credentials['password']}".encode()
    ).decode()
    request.add_header("Authorization", "Basic " + token)
    with urllib.request.urlopen(request, timeout=15) as response:
        return [json.loads(line) for line in response.read().decode().splitlines() if line]


def sql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r") + "'"


def await_event(tools_root: Path, raw: str, timeout: int = 120) -> dict | None:
    table = "logger.messages"
    query = (
        f"SELECT * FROM {table} WHERE message IN "
        f"({sql_literal(raw)}, {sql_literal(raw + chr(10))}) ORDER BY timestamp DESC LIMIT 1"
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            rows = clickhouse_query(tools_root, query)
            if rows:
                return rows[0]
        except (OSError, ValueError):
            pass
        time.sleep(2)
    return None


def values_match(field: str, expected, actual, event: dict) -> bool:
    if field == "time" and "timestamp_modified" in event.get("tags", []):
        actual = event.get("original_time")
    if field in {"time", "start_time", "recv_time", "original_time"} and expected and actual:
        try:
            from datetime import datetime
            expected_time = datetime.fromisoformat(str(expected).replace("Z", "+00:00"))
            actual_time = datetime.fromisoformat(str(actual).replace("Z", "+00:00"))
            return expected_time == actual_time
        except ValueError:
            return False
    return actual == expected


def compare_event(actual: dict, fixture: dict) -> list[dict]:
    errors = []
    for field, expected in fixture.get("expected_saturn", fixture.get("expected", {})).items():
        column = "timestamp" if field == "time" else field
        value = actual.get(column)
        if not values_match(field, expected, value, actual):
            errors.append({"field": field, "error": "value_mismatch", "expected": expected, "actual": value})
    for field in fixture.get("required_fields", []):
        column = "timestamp" if field == "time" else field
        if actual.get(column) in (None, ""):
            errors.append({"field": field, "error": "missing_or_empty", "actual": actual.get(column)})
    return errors


def running_config_test(tools_root: Path, timeout: int = 300) -> dict:
    command = [
        "docker", "exec", "mp-saturn-agent", "bash", "-lc",
        "source /bin/init_config.sh && /usr/share/logstash/bin/logstash --config.test_and_exit",
    ]
    try:
        completed = run_command(command, tools_root / "docker", timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "error": "running_config_test_failed", "detail": str(error)}
    return {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-10000:],
        "stderr": completed.stderr[-10000:],
    }


def run_end_to_end(tools_root: Path, fixtures: dict, startup_timeout: int = 120) -> dict:
    ensure_no_fixed_containers()
    started = False
    try:
        startup = start_stack(tools_root)
        if not startup["ok"]:
            return {"ok": False, "stage": "startup", "startup": startup}
        started = True
        positives = fixtures.get("positive", [])
        raw_logs = [item["event"]["message"] for item in positives]
        wait_for_pipelines(startup_timeout)
        deadline = time.monotonic() + startup_timeout
        while True:
            try:
                send_tcp(raw_logs)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(2)
        results = []
        for index, fixture in enumerate(positives):
            raw = fixture["event"]["message"]
            actual = await_event(tools_root, raw, startup_timeout)
            errors = [] if actual else [{"error": "event_not_found_in_clickhouse"}]
            if actual:
                errors.extend(compare_event(actual, fixture))
            results.append({
                "index": index,
                "name": fixture.get("name"),
                "ok": not errors,
                "errors": errors,
                "actual": actual,
            })
        return {
            "ok": all(item["ok"] for item in results),
            "stage": "raw_to_clickhouse",
            "results": results,
        }
    except (OSError, SaturnTestError) as error:
        return {"ok": False, "stage": "runner", "error": f"{type(error).__name__}: {error}"}
    finally:
        if started:
            stop_stack(tools_root)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate generated MP package in saturn-tools.")
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--pipelines", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"ok": False, "end_to_end": None}
    try:
        with substituted_pipelines(args.tools, args.pipelines, args.package):
            fixtures = json.loads(args.fixtures.read_text(encoding="utf-8"))
            report["end_to_end"] = run_end_to_end(args.tools, fixtures)
            report["ok"] = report["end_to_end"]["ok"]
    except (OSError, SaturnTestError) as error:
        report["error"] = f"{type(error).__name__}: {error}"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
