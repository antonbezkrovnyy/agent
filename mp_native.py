import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any


VALIDATOR_SOURCE = Path(__file__).parent / "native" / "mp_rule_validator.rs"
VALIDATOR_DOCKERFILE = Path(__file__).parent / "native" / "Dockerfile"
DEFAULT_MPSIEM = Path(os.getenv("MP_SIEM_SOURCE", ".tmp_logicore/mpsiem"))
DEFAULT_IMAGE = os.getenv("MP_RUST_BUILD_IMAGE", "rustlang/rust:nightly-bullseye")


def decode_json_report(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "ok" in value:
            return value
    raise ValueError(f"native validator returned no JSON report: {stdout[-1000:]}")


def write_crate(directory: Path, mpsiem: Path, dependency_path: str | None = None) -> None:
    (directory / "src").mkdir(parents=True)
    (directory / "src" / "main.rs").write_text(
        VALIDATOR_SOURCE.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )
    lockfile = mpsiem / "Cargo.lock"
    if lockfile.is_file():
        (directory / "Cargo.lock").write_bytes(lockfile.read_bytes())
    dependency_path = dependency_path or mpsiem.resolve().as_posix()
    (directory / "Cargo.toml").write_text(
        "[package]\n"
        "name = \"mp_rule_validator\"\n"
        "version = \"0.1.0\"\n"
        "edition = \"2021\"\n\n"
        "[dependencies]\n"
        f"mpsiem = {{ path = \"{dependency_path}\" }}\n"
        "serde = { version = \"1.0\", features = [\"derive\"] }\n"
        "serde_json = \"1.0\"\n"
        "serde_yaml = \"0.9\"\n"
        "vrl = { version = \"0.27.0\", features = [\"test\", \"test_framework\"] }\n",
        encoding="utf-8",
        newline="\n",
    )


def validator_image(mpsiem: str | Path) -> str:
    mpsiem_path = Path(mpsiem).resolve()
    try:
        revision = subprocess.run(
            ["git", "-C", str(mpsiem_path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        revision = "unknown"
    digest = hashlib.sha256()
    digest.update(revision.encode())
    digest.update(VALIDATOR_SOURCE.read_bytes())
    digest.update(VALIDATOR_DOCKERFILE.read_bytes())
    lockfile = mpsiem_path / "Cargo.lock"
    if lockfile.is_file():
        digest.update(lockfile.read_bytes())
    return f"mp-rule-validator:{digest.hexdigest()[:16]}"


def ensure_validator_image(mpsiem: str | Path, timeout: int = 1800) -> dict[str, Any]:
    mpsiem_path = Path(mpsiem).resolve()
    if not (mpsiem_path / "Cargo.toml").is_file():
        return {"ok": False, "error": "mpsiem_source_not_found", "path": str(mpsiem_path)}
    image = validator_image(mpsiem_path)
    inspected = subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, text=True, timeout=30
    )
    if inspected.returncode == 0:
        return {"ok": True, "image": image, "built": False}
    with tempfile.TemporaryDirectory(prefix="mp-validator-image-") as temporary:
        context = Path(temporary)
        shutil.copytree(
            mpsiem_path,
            context / "mpsiem",
            ignore=shutil.ignore_patterns(".git", "target"),
        )
        write_crate(context / "validator", mpsiem_path, "/build/mpsiem")
        command = [
            "docker", "build", "--build-arg", f"RUST_BUILD_IMAGE={DEFAULT_IMAGE}",
            "-t", image, "-f", str(VALIDATOR_DOCKERFILE.resolve()), str(context),
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            return {
                "ok": False,
                "error": "validator_image_build_failed",
                "detail": f"{type(error).__name__}: {error}",
                "image": image,
            }
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": "validator_image_build_failed",
            "image": image,
            "exit_code": completed.returncode,
            "stdout": completed.stdout[-8000:],
            "stderr": completed.stderr[-8000:],
        }
    return {"ok": True, "image": image, "built": True}


def completed_report(completed: subprocess.CompletedProcess) -> dict[str, Any]:
    try:
        report = decode_json_report(completed.stdout)
    except ValueError as error:
        return {
            "ok": False,
            "error": "native_validator_failed",
            "detail": str(error),
            "exit_code": completed.returncode,
            "stdout": completed.stdout[-3000:],
            "stderr": completed.stderr[-6000:],
        }
    report["native_exit_code"] = completed.returncode
    if completed.returncode != 0 and report.get("ok"):
        report["ok"] = False
        report["error"] = "native_validator_exit_failed"
    if completed.returncode != 0 and completed.stderr:
        report["stderr"] = completed.stderr[-6000:]
    return report


def run_validator(
    arguments: list[str],
    mpsiem: str | Path = DEFAULT_MPSIEM,
    mode: str = "docker",
    timeout: int = 600,
) -> dict[str, Any]:
    mpsiem_path = Path(mpsiem).resolve()
    if not (mpsiem_path / "Cargo.toml").is_file():
        return {"ok": False, "error": "mpsiem_source_not_found", "path": str(mpsiem_path)}
    if mode == "docker":
        image = ensure_validator_image(mpsiem_path)
        if not image["ok"]:
            return image
        command = ["docker", "run", "--rm", image["image"], *arguments]
    elif mode == "local":
        with tempfile.TemporaryDirectory(prefix="mp-rule-validator-") as temporary:
            crate = Path(temporary)
            write_crate(crate, mpsiem_path)
            command = [
                os.getenv("CARGO", "cargo"), "run", "--release", "--manifest-path",
                str(crate / "Cargo.toml"), "--", *arguments,
            ]
            try:
                completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
            except (OSError, subprocess.TimeoutExpired) as error:
                return {
                    "ok": False,
                    "error": "native_validator_start_failed",
                    "detail": f"{type(error).__name__}: {error}",
                    "mode": mode,
                }
            return completed_report(completed)
    else:
        return {"ok": False, "error": "unknown_validator_mode", "mode": mode}
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "ok": False,
            "error": "native_validator_start_failed",
            "detail": f"{type(error).__name__}: {error}",
            "mode": mode,
        }
    return completed_report(completed)


def validate_rules(
    rules: str | Path,
    fixtures: str | Path | None = None,
    mpsiem: str | Path = DEFAULT_MPSIEM,
    mode: str = "docker",
) -> dict[str, Any]:
    rules_path = Path(rules).resolve()
    if mode == "local":
        arguments = ["validate", str(rules_path)]
        if fixtures:
            arguments.append(str(Path(fixtures).resolve()))
        return run_validator(arguments, mpsiem, mode)
    image = ensure_validator_image(mpsiem)
    if not image["ok"]:
        return image
    with tempfile.TemporaryDirectory(prefix="mp-native-input-") as temporary:
        mounted = Path(temporary)
        shutil.copy2(rules_path, mounted / rules_path.name)
        arguments = ["validate", f"/rules/{rules_path.name}"]
        if fixtures:
            fixture_path = Path(fixtures).resolve()
            shutil.copy2(fixture_path, mounted / fixture_path.name)
            arguments.append(f"/rules/{fixture_path.name}")
        command = [
            "docker", "run", "--rm", "-v", f"{mounted}:/rules:ro", image["image"], *arguments
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired) as error:
            return {
                "ok": False,
                "error": "native_validator_start_failed",
                "detail": f"{type(error).__name__}: {error}",
                "mode": mode,
            }
    return completed_report(completed)


def validate_rules_from_text(
    yaml_text: str,
    fixtures: dict[str, Any],
    mpsiem: str | Path = DEFAULT_MPSIEM,
    mode: str = "docker",
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mp-native-package-") as temporary:
        root = Path(temporary)
        rules = root / "norm_rules.yml"
        fixture_path = root / "fixtures.json"
        rules.write_text(yaml_text, encoding="utf-8", newline="\n")
        fixture_path.write_text(json.dumps(fixtures, ensure_ascii=False), encoding="utf-8")
        return validate_rules(rules, fixture_path, mpsiem, mode)


def execute_text_captures(
    document: dict[str, Any],
    mpsiem: str | Path = DEFAULT_MPSIEM,
    mode: str = "docker",
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mp-text-captures-") as temporary:
        input_path = Path(temporary) / "patterns.json"
        input_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        if mode == "local":
            return run_validator(["captures", str(input_path)], mpsiem, mode)
        image = ensure_validator_image(mpsiem)
        if not image["ok"]:
            return image
        command = [
            "docker", "run", "--rm", "-v", f"{Path(temporary).resolve()}:/rules:ro",
            image["image"], "captures", "/rules/patterns.json",
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as error:
            return {
                "ok": False,
                "error": "native_capture_runner_start_failed",
                "detail": f"{type(error).__name__}: {error}",
            }
    return completed_report(completed)


def capabilities(mpsiem: str | Path = DEFAULT_MPSIEM, mode: str = "docker") -> dict[str, Any]:
    return run_validator(["capabilities"], mpsiem, mode)


def main() -> None:
    parser = argparse.ArgumentParser(description="Strict native MP SIEM YAML validator.")
    parser.add_argument("command", choices=["validate", "captures", "capabilities", "build"])
    parser.add_argument("rules", nargs="?", type=Path)
    parser.add_argument("--fixtures", type=Path)
    parser.add_argument("--mpsiem", type=Path, default=DEFAULT_MPSIEM)
    parser.add_argument("--mode", choices=["docker", "local"], default="docker")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command in {"validate", "captures"} and not args.rules:
        parser.error(f"{args.command} requires an input file")
    if args.command == "build":
        result = ensure_validator_image(args.mpsiem)
    elif args.command == "capabilities":
        result = capabilities(args.mpsiem, args.mode)
    elif args.command == "captures":
        result = execute_text_captures(
            json.loads(args.rules.read_text(encoding="utf-8")), args.mpsiem, args.mode
        )
    else:
        result = validate_rules(args.rules, args.fixtures, args.mpsiem, args.mode)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
