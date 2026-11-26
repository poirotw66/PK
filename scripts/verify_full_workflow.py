"""Build and exercise a complete synthetic tournament without contacting deployed Sites."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--font",
        type=Path,
        required=True,
        help="A licensed local CJK font for the actual PDF export.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "squash-scheduler/output/verification"
        / ("full-workflow-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")),
    )
    parser.add_argument("--node", default="node")
    args = parser.parse_args()
    font, output = args.font.resolve(), args.output.resolve()
    if not font.is_file() or output.exists():
        parser.error(
            "Use an existing font and a new output directory; existing workspaces are never replaced."
        )
    node = shutil.which(args.node)
    if not node:
        parser.error("Node 22 or later is required.")
    version = subprocess.check_output([node, "--version"], text=True).strip()
    if int(version.lstrip("v").split(".")[0]) < 22:
        parser.error("Node 22 or later is required.")
    env = {
        **os.environ,
        "PATH": str(Path(node).parent) + os.pathsep + os.environ.get("PATH", ""),
        "PYTHONPATH": str(ROOT / "squash-scheduler/src")
        + os.pathsep
        + os.environ.get("PYTHONPATH", ""),
    }
    admin, reader = ROOT / "sites/squash-admin", ROOT / "sites/squash-reader"
    if not (admin / "node_modules/vinext").exists():
        parser.error(
            "Install the locked admin dependencies first with npm run install:ci in sites/squash-admin."
        )
    if not (admin / ".openai/hosting.json").exists():
        (admin / ".openai").mkdir(exist_ok=True)
        shutil.copy2(admin / "hosting.example.json", admin / ".openai/hosting.json")
    harness = ROOT / "scripts/verify_project_workflow.py"
    run(
        [
            sys.executable,
            str(harness),
            "prepare",
            "--output",
            str(output),
            "--font",
            str(font),
        ],
        ROOT,
        env,
    )
    # Standings tests require a round-robin fixture in addition to the knockout workflow.
    run(
        [
            sys.executable,
            "examples/run_demo.py",
            "--output",
            str(output / "reader-fixture"),
            "--no-pdf",
        ],
        ROOT / "squash-scheduler",
        env,
    )
    run(
        [
            sys.executable,
            "build.py",
            "--scheduler",
            str(ROOT / "squash-scheduler"),
            "--workspace",
            str(output / "reader-fixture/tournament"),
        ],
        reader,
        env,
    )
    run([sys.executable, "scripts/build-worker.py"], reader, env)
    run(
        [
            node,
            "--test",
            *[
                str(path.relative_to(reader))
                for path in sorted((reader / "tests").glob("*.test.mjs"))
            ],
        ],
        reader,
        env,
    )
    run([node, "scripts/run-framework.mjs", "build"], admin, env)
    run([node, "node_modules/typescript/bin/tsc", "--noEmit"], admin, env)
    bridge_env = {
        **env,
        "PK_WORKFLOW_INPUTS": str(output),
        "PK_WORKFLOW_EVIDENCE": str(output),
        "PK_READER_ROOT": str(reader),
    }
    run([node, "scripts/verify-contracts.mjs"], admin, bridge_env)
    run(
        [
            sys.executable,
            str(harness),
            "finish",
            "--output",
            str(output),
            "--cloud-return",
            str(output / "cloud-return.json"),
        ],
        ROOT,
        env,
    )
    receipt = json.loads((output / "local-finish-receipt.json").read_text())
    if receipt["confirmed_results"] != 3 or not receipt["document_qa"]:
        raise RuntimeError("The tournament or document verification did not complete.")
    print(
        "Verified upload, scheduling, cloud results, advancement, correction, backup restoration, local return and PDF/Excel."
    )
    print(output / "local-finish-receipt.json")


if __name__ == "__main__":
    main()
