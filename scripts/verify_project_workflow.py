"""Exercise the local HTTP workflow with synthetic data, before and after a cloud bridge.

Run with squash-scheduler/.venv/bin/python. This never contacts deployed Sites.
Prepare writes admin-contract.json and public-snapshot.json for the isolated Node bridge.
Finish consumes that bridge's cloud-return.json, then creates real PDF and Excel documents.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "squash-scheduler/src"))

from openpyxl import Workbook
from squash_scheduler.operator_server import create_server
from squash_scheduler.public_feed import PublicFeed
from squash_scheduler.storage.repository import Repository


def write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


class LocalHttp:
    def __init__(self, repository: Repository):
        self.server = create_server(repository, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        html = urlopen(self.origin + "/setup").read().decode()
        self.token = json.loads(
            re.search(r'<script id="boot"[^>]*>(.*?)</script>', html).group(1)
        )["token"]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def request(
        self,
        path: str,
        body=None,
        *,
        raw: bytes | None = None,
        headers: dict | None = None,
    ):
        request_headers = {
            "X-Squash-Token": self.token,
            "Origin": self.origin,
            **(headers or {}),
        }
        payload = raw
        if body is not None:
            payload = json.dumps(body).encode()
            request_headers["Content-Type"] = "application/json"
        if raw is not None:
            request_headers["Content-Type"] = "application/octet-stream"
        request = Request(self.origin + path, data=payload, headers=request_headers)
        try:
            response = urlopen(request, timeout=120)
            return json.load(response)
        except HTTPError as error:
            raise RuntimeError(
                f"{path}: {error.code} {error.read().decode()}"
            ) from error

    def apply(self, action: str, **values):
        state = self.request("/api/setup-state")
        preview = self.request(
            "/api/preview",
            {
                "action": action,
                "revision_id": state["revision_id"],
                "actor": "synthetic-workflow",
                "reason": "Synthetic full workflow verification",
                **values,
            },
        )
        return self.request(
            "/api/confirm", {"id": preview["id"], "hash": preview["hash"]}
        )


def prepare(output: Path, font: Path | None):
    if font is not None and (
        not font.is_file() or not 0 < font.stat().st_size <= 32 * 1024 * 1024
    ):
        raise ValueError("--font must be an existing local font no larger than 32 MiB.")
    repository = Repository(output / "workspace")
    if (repository.root / "working/current.json").exists():
        raise ValueError(
            "Verification workspace already exists; select a fresh --output instead of replacing it."
        )
    http = LocalHttp(repository)
    try:
        workbook = Workbook()
        for row in [
            ["姓名", "組別"],
            *[
                [f"Synthetic Player {index}", "Synthetic knockout"]
                for index in range(1, 5)
            ],
        ]:
            workbook.active.append(row)
        stream = io.BytesIO()
        workbook.save(stream)
        roster = stream.getvalue()
        (output / "synthetic-roster.xlsx").write_bytes(roster)
        upload = http.request("/api/intake?filename=synthetic-roster.xlsx", raw=roster)
        settings = {
            "name": "Synthetic full workflow",
            "venue": "Synthetic courts",
            "dates": ["2030-01-01"],
            "start": "08:00",
            "end": "17:30",
            "courts": ["Synthetic A", "Synthetic B"],
            "grid": 15,
            "play": 20,
            "buffer": 10,
            "rest": 60,
            "daily_limit": 5,
            "transfer": 0,
            "ranking_delay": 0,
            "participant_scope": "confirmed_only",
            "best_of": 3,
            "target": 11,
            "late_arrival_rule": "Synthetic verification terms: referee confirms late arrivals.",
            "division_classifications": {
                "Synthetic knockout": {
                    "category": "competitive",
                    "gender_category": "mixed",
                }
            },
        }
        settings.update(
            walkover_rule={
                "include_in_rankings": True,
                "counts_as_played": False,
                "winner_points": 2,
                "loser_points": 0,
            },
            retirement_rule={
                "include_in_rankings": True,
                "counts_as_played": True,
                "winner_points": 2,
                "loser_points": 0,
            },
            double_forfeit_rule={
                "include_in_rankings": True,
                "counts_as_played": False,
                "side_a_points": 0,
                "side_b_points": 0,
            },
            ranking_denominator_rule={
                "played": True,
                "walkover": True,
                "retired": True,
                "double_forfeit": False,
            },
        )
        preview = http.request(
            "/api/setup-preview",
            {
                "actor": "synthetic-workflow",
                "reason": "Reviewed synthetic four-player roster",
                "uploads": [upload["id"]],
                "rows": [
                    {**row, "source_id": upload["id"], "identity": f"player-{index}"}
                    for index, row in enumerate(upload["rows"])
                ],
                "settings": settings,
            },
        )
        http.request(
            "/api/setup-confirm", {"id": preview["id"], "hash": preview["hash"]}
        )
        division = repository.load().divisions[0]
        http.apply(
            "format",
            division_id=division.id,
            format={
                "template_id": "single_elimination",
                "pools": [],
                "bronze": False,
                "encouragement": False,
            },
        )
        http.apply("draw", division_id=division.id, seed=42)
        draw = repository.load().draws[-1]
        http.apply("approve_draw", draw_id=draw["id"])
        http.apply("schedule")
        if font is not None:
            metadata = base64.b64encode(
                json.dumps(
                    {"actor": "synthetic-workflow", "revision_id": repository.load().id}
                ).encode()
            ).decode()
            http.request(
                "/api/font",
                raw=font.read_bytes(),
                headers={"X-Upload-Metadata": metadata},
            )
        contract = http.request("/api/cloud-export")
        public = PublicFeed().read(repository)
        write_json(output / "admin-contract.json", contract)
        write_json(output / "public-snapshot.json", public)
        matches = sorted(
            repository.load().matches,
            key=lambda match: (match.round, match.bracket_position),
        )
        receipt = {
            "phase": "prepared",
            "workspace": str(repository.root),
            "source_revision": contract["source_revision"],
            "matches": len(matches),
            "admin_contract": str(output / "admin-contract.json"),
            "public_snapshot": str(output / "public-snapshot.json"),
            "font_imported": font is not None,
            "http_steps": [
                "upload-xlsx",
                "setup-preview-confirm",
                "format-preview-confirm",
                "draw-preview-confirm",
                "approve-draw-preview-confirm",
                "schedule-preview-confirm",
                *(["font-upload"] if font is not None else []),
                "cloud-export",
            ],
            "production_requests": 0,
        }
        write_json(output / "local-prepare-receipt.json", receipt)
        print(json.dumps(receipt, indent=2))
    finally:
        http.close()


def finish(output: Path, cloud_return: Path, timeout: int):
    repository = Repository(output / "workspace")
    prepared = json.loads((output / "local-prepare-receipt.json").read_text())
    current = repository.load()
    if (
        prepared.get("phase") != "prepared"
        or prepared.get("source_revision") != current.id
        or current.config["event"]["name"] != "Synthetic full workflow"
    ):
        raise ValueError(
            "Finish only accepts the unchanged synthetic workspace from this harness's prepare phase."
        )
    http = LocalHttp(repository)
    try:
        before = repository.load()
        envelope = json.loads(cloud_return.read_text())
        preview = http.request(
            "/api/cloud-return-preview",
            {
                "revision_id": before.id,
                "actor": "synthetic-workflow",
                "reason": "Review isolated cloud bridge results",
                "envelope": envelope,
            },
        )
        write_json(output / "local-return-preview.json", preview)
        confirmed = http.request(
            "/api/confirm", {"id": preview["id"], "hash": preview["hash"]}
        )
        revision = repository.load().id
        assert revision != before.id
        job = http.request("/api/export", {"revision_id": revision})
        deadline = time.monotonic() + timeout
        while job["status"] == "running" and time.monotonic() < deadline:
            time.sleep(1)
            job = next(
                row
                for row in http.request("/api/state")["jobs"]
                if row["id"] == job["id"]
            )
        if job["status"] != "completed":
            raise RuntimeError(f"Document export did not complete: {job}")
        directory = repository.root / "operator-exports" / job["id"]
        manifest = json.loads((directory / "manifest.json").read_text())
        assert manifest["qa"]["passed"]
        artifacts = {}
        for filename in ["booklet.pdf", "schedule.xlsx"]:
            file = directory / filename
            assert file.is_file() and file.stat().st_size > 0
            artifacts[filename] = {
                "path": str(file),
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                "bytes": file.stat().st_size,
            }
        result_count = sum(
            result.get("status") == "confirmed" for result in repository.load().results
        )
        receipt = {
            "phase": "finished",
            "revision_before": before.id,
            "revision_after": revision,
            "confirmed": confirmed,
            "confirmed_results": result_count,
            "document_qa": manifest["qa"]["passed"],
            "pdf_pages": len(manifest["qa"]["pages"]),
            "artifacts": artifacts,
            "job": job,
            "production_requests": 0,
            "scope": "Real loopback local HTTP upload/setup/scheduling/export and cloud-return confirmation; cloud operations are validated by the separate isolated Node bridge receipt. No production writes.",
        }
        write_json(output / "local-finish-receipt.json", receipt)
        print(json.dumps(receipt, indent=2))
    finally:
        http.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "finish"])
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "squash-scheduler/output/verification/project-workflow-local",
    )
    parser.add_argument("--font", type=Path)
    parser.add_argument("--cloud-return", type=Path)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.phase == "prepare":
        prepare(output, args.font)
    else:
        finish(output, args.cloud_return or output / "cloud-return.json", args.timeout)


if __name__ == "__main__":
    main()
