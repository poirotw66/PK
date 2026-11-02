"""Persist check results so interrupted tool observation cannot erase verification evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def verify(output: Path, pdf: bool) -> int:
    root = Path(__file__).resolve().parents[1]
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    state = {
        "pid": os.getpid(),
        "started_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "checks": [],
    }

    def source_hash() -> str:
        inputs = [
            p
            for directory in ("src", "tests", "examples", "scripts", "schemas")
            for p in (root / directory).rglob("*")
            if p.is_file()
            and p.suffix
            in {".py", ".json", ".md", ".html", ".css", ".js", ".mjs", ".woff2", ".ttf", ".txt"}
            and "__pycache__" not in p.parts
        ]
        inputs.extend([root / "pyproject.toml", root / "uv.lock"])
        values = [
            (p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(inputs)
        ]
        return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode("utf-8")).hexdigest()

    state["source_hash_at_start"] = source_hash()

    def save() -> None:
        temporary = output / "state.pending.json"
        temporary.write_text(json.dumps(state, indent=2), "utf-8")
        temporary.replace(output / "state.json")

    commands = [
        ("lint", [sys.executable, "-m", "ruff", "check", "src", "tests", "examples", "scripts"]),
        (
            "format",
            [
                sys.executable,
                "-m",
                "ruff",
                "format",
                "--check",
                "src",
                "tests",
                "examples",
                "scripts",
            ],
        ),
        ("types", [sys.executable, "-m", "mypy", "src"]),
        ("tests", [sys.executable, "-m", "pytest", "-q", "--junitxml", str(output / "pytest.xml")]),
    ]
    save()
    try:
        for name, command in commands:
            state["current_check"] = name
            save()
            environment = {
                **os.environ,
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "SQUASH_PDF_SMOKE": "1" if pdf else "0",
            }
            with (output / f"{name}.log").open("w", encoding="utf-8") as log:
                completed = subprocess.run(
                    command,
                    cwd=root,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            state["checks"].append(
                {
                    "name": name,
                    "exit_code": completed.returncode,
                    "log": f"{name}.log",
                    "completed_at": datetime.now(UTC).isoformat(),
                }
            )
            save()
        state["status"] = (
            "passed" if all(c["exit_code"] == 0 for c in state["checks"]) else "failed"
        )
        state["source_hash_at_end"] = source_hash()
        if state["source_hash_at_end"] != state["source_hash_at_start"]:
            state.update(
                status="failed", error="Sources changed during verification; rerun required"
            )
    except BaseException as exc:
        state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        state["completed_at"] = datetime.now(UTC).isoformat()
        save()
    print(json.dumps(state, indent=2))
    return 0 if state["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/verification/latest"))
    parser.add_argument("--pdf", action="store_true")
    args = parser.parse_args()
    raise SystemExit(verify(args.output, args.pdf))
