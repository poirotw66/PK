"""Local operator use cases: preview, exact confirmation, immutable repository save."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID

from .domain.draws import run_draw
from .domain.encouragement import eligibility_report
from .domain.encouragement_draws import apply_draw, propose_draw
from .domain.matches import generate_matches
from .domain.models import Assignment, Snapshot, canonical_hash, content_hash, new_id, utcnow
from .domain.rankings import effective_rules, import_results, resolve_side
from .domain.revisions import approve
from .domain.seeds import configure_seeds
from .domain.validation import target_basis, validate
from .public_feed import PublicFeed
from .scheduling import solve_schedule, validate_schedule
from .storage.repository import Repository, atomic_json


class Conflict(ValueError):
    pass


class OperatorService:
    def __init__(self, repository: Repository):
        self.repo = repository
        self.base_repo = repository
        self.plans: dict[str, dict[str, Any]] = {}
        from .operator_records import load_jobs

        self.jobs: dict[str, dict[str, Any]] = load_jobs(repository)
        self.restore_plans: dict[str, dict[str, Any]] = {}
        self.history_cache: tuple[str, str, list[dict]] | None = None
        self.guard = threading.RLock()
        self.workload_guard = threading.Lock()
        self.workload_cache: dict[tuple[str, str, str, str], dict] = {}
        self.public_feed = PublicFeed()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="booklet")

    def close(self) -> None:
        self.executor.shutdown(wait=True)

    def state(self) -> dict:
        with self.guard:
            return self._state()

    def public_snapshot(self) -> dict:
        with self.guard:
            return self.public_feed.read(self.repo)

    def public_version(self) -> dict:
        with self.guard:
            return self.public_feed.version(self.repo)

    def workload(
        self,
        mode: str = "configured",
        *,
        revision_id: str | None = None,
        workspace_id: str | None = None,
    ) -> dict:
        """Read-only, revision-bound audit; expensive analysis is off the state path."""
        from .operator_workload import build_operator_workload

        if mode not in {"configured", "requested"}:
            raise ValueError("請選擇目前規則或 4 場／60 分鐘試算。")
        with self.workload_guard:
            with self.guard:
                root = str(self.repo.root)
                identity = canonical_hash({"workspace": root})[:24]
                snapshot = self.repo.load()
                fingerprint = content_hash(snapshot)
                if (revision_id is not None and revision_id != snapshot.id) or (
                    workspace_id is not None and workspace_id != identity
                ):
                    raise Conflict("工作區或版本已更新，請重新整理後再檢查選手負荷。")
                key = (root, snapshot.id, fingerprint, mode)
                if key in self.workload_cache:
                    return deepcopy(self.workload_cache[key])
            report = build_operator_workload(
                snapshot,
                policy={"minimum_rest_minutes": 60, "max_matches_per_day": 4}
                if mode == "requested"
                else None,
            )
            with self.guard:
                current = self.repo.load()
                if (
                    str(self.repo.root) != root
                    or current.id != snapshot.id
                    or content_hash(current) != fingerprint
                ):
                    raise Conflict("分析期間賽事資料已更新，請重新整理後再檢查。")
                value = {**report, "workspace_id": identity, "snapshot_content_hash": fingerprint}
                self.workload_cache[key] = deepcopy(value)
                while len(self.workload_cache) > 4:
                    del self.workload_cache[next(iter(self.workload_cache))]
                return value

    def _state(self) -> dict:
        from .operator_actions import state_fields
        from .operator_records import history

        snapshot = self.repo.load()
        cache_key = (str(self.repo.root), snapshot.id)
        if self.history_cache is None or self.history_cache[:2] != cache_key:
            self.history_cache = (*cache_key, history(self.repo))
        with self.guard:
            jobs = [
                {k: v for k, v in job.items() if k != "directory"} for job in self.jobs.values()
            ]
        registrations = {r.id: r for r in snapshot.registrations}
        names = {p.id: p.display_name for p in snapshot.participants}
        entry_names = {
            e.id: names[registrations[e.registration_id].participant_id] for e in snapshot.entries
        }
        divisions = {d.id: d for d in snapshot.divisions}
        stages = {s.id: s for s in snapshot.stages}
        results = {r["match_id"]: r for r in snapshot.results if r.get("status") == "confirmed"}
        assignments = {a.match_id: a for a in snapshot.assignments}

        def side_name(side):
            entry_id = resolve_side(snapshot, side)
            if entry_id:
                return entry_names.get(entry_id, "未確認選手")
            if side.type in {"match_winner", "match_loser"}:
                source = next((m for m in snapshot.matches if m.id == side.match_id), None)
                return f"{source.display_code or source.id[:8] if source else '來源'}場{'勝者' if side.type == 'match_winner' else '敗者'}"
            return "對手待確認"

        return {
            **state_fields(snapshot),
            "history": deepcopy(self.history_cache[2]),
            "workspace_label": self.repo.root.name,
            "workspaces": self.workspaces(),
            "backups": self.backups(),
            "revision_id": snapshot.id,
            "workspace_id": canonical_hash({"workspace": str(self.repo.root)})[:24],
            "content_hash": content_hash(snapshot),
            "name": snapshot.config.get("event", {}).get("name") or "壁球賽事",
            "timezone": snapshot.config.get("event", {}).get("timezone") or "Asia/Taipei",
            "actor": snapshot.config.get("event", {}).get("actor_id") or "",
            "notice": snapshot.config.get("publication", {}).get("draft_note") or "",
            "courts": snapshot.config.get("courts", []),
            "event_dates": snapshot.config.get("event", {}).get("event_dates", []),
            "timing_defaults": snapshot.config.get("timing_defaults", {}),
            "player_workload": snapshot.config.get("player_workload") or {},
            "schedule_reviews": [
                deepcopy(c) for c in reversed(snapshot.changes) if c.get("schedule_review")
            ][:10],
            "entries": [
                {
                    "id": e.id,
                    "division_id": e.effective_division_id,
                    "name": entry_names[e.id],
                    "status": e.status,
                }
                for e in snapshot.entries
            ],
            "divisions": [
                {
                    "id": d.id,
                    "name": d.name,
                    "capacity": (
                        sum(
                            e.effective_division_id == d.id and e.status == "active"
                            for e in snapshot.entries
                        )
                        if (d.format_plan or {})
                        .get("template_id", "")
                        .startswith(("round_robin", "pools_"))
                        else (d.format_plan or {}).get("bracket_capacity")
                    ),
                    "seed_rules": {**snapshot.config.get("draw_defaults", {}), **d.draw_overrides},
                    "seed_editable": not any(
                        m.division_id == d.id and m.status in {"in_progress", "completed"}
                        for m in snapshot.matches
                    ),
                    "draw": next(
                        (
                            deepcopy(r)
                            for r in reversed(snapshot.draws)
                            if r.get("division_id") == d.id
                            and r.get("kind") not in {"encouragement", "deferred_slots"}
                            and r.get("status") != "superseded"
                        ),
                        None,
                    ),
                }
                for d in snapshot.divisions
            ],
            "matches": [
                {
                    "id": m.id,
                    "code": m.display_code or m.id[:8],
                    "division_id": m.division_id,
                    "division": divisions[m.division_id].name,
                    "stage": stages[m.stage_id].label
                    or {"placement": "名次賽", "elimination": "淘汰賽", "round_robin": "循環賽"}[
                        stages[m.stage_id].type
                    ],
                    "status": m.status,
                    "side_a": side_name(m.side_a),
                    "side_b": side_name(m.side_b),
                    "entry_a": resolve_side(snapshot, m.side_a),
                    "entry_b": resolve_side(snapshot, m.side_b),
                    "result": results.get(m.id),
                    "best_of": effective_rules(snapshot, m.stage_id).get("best_of_games"),
                    "time": assignments[m.id].start_at.isoformat() if m.id in assignments else None,
                    "assignment": assignments[m.id].model_dump(mode="json")
                    if m.id in assignments
                    else None,
                    "schedule_editable": m.status
                    not in {"in_progress", "completed", "cancelled", "excluded"}
                    and not (
                        m.id in assignments
                        and all(
                            (
                                bool(
                                    assignments[m.id].locks.get("time")
                                    or assignments[m.id].locks.get("start_at")
                                ),
                                bool(
                                    assignments[m.id].locks.get("court")
                                    or assignments[m.id].locks.get("court_id")
                                ),
                            )
                        )
                    ),
                    "court": next(
                        (
                            c["label"]
                            for c in snapshot.config.get("courts", [])
                            if m.id in assignments and c["id"] == assignments[m.id].court_id
                        ),
                        "未排程",
                    ),
                }
                for i, m in enumerate(snapshot.matches)
            ],
            "eligibility": [
                eligibility_report(snapshot, d.id)
                for d in snapshot.divisions
                if d.name in {"國中男子組", "國中女子組"}
            ],
            "counts": {
                "entries": len(snapshot.entries),
                "matches": len(snapshot.matches),
                "completed": sum(m.status == "completed" for m in snapshot.matches),
            },
            "jobs": jobs,
        }

    def _execute(self, snapshot: Snapshot, operation: dict) -> tuple[Snapshot, dict]:
        working = snapshot.model_copy(deep=True)
        action, actor, reason = operation["action"], operation["actor"], operation["reason"]
        if (
            not isinstance(actor, str)
            or not actor.strip()
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError("請填寫操作者與本次異動原因。")
        division_id = operation.get("division_id", "")
        if action in {
            "live_match",
            "reserve",
            "entry_change",
            "approve_entry_change",
            "local_replan",
        }:
            from .operator_actions import execute_action

            working, summary = execute_action(snapshot, operation)
        elif action == "restore_revision":
            from .operator_records import execute_restore_revision

            working = execute_restore_revision(self.repo, snapshot, operation["restore_proposal"])
            summary = operation["restore_summary"]
        elif action == "seeds":
            audit = configure_seeds(working, division_id, operation["mode"], operation["seeds"])
            summary = {
                "title": "儲存種子設定",
                "division_id": division_id,
                "seed_rules": audit["after"],
                "before": audit["before"],
                "notice": "儲存後須重新建立及確認主賽籤表。"
                if audit["requires_redraw"]
                else "種子設定與目前相同，保留原籤表。",
            }
        elif action == "draw":
            if any(
                m.division_id == division_id and m.status in {"in_progress", "completed"}
                for m in working.matches
            ):
                raise ValueError("此組已開始比賽，不能重新建立主賽抽籤。")
            draw = run_draw(working, division_id, operation["seed"], actor, reason=reason)
            if not draw.get("output"):
                raise ValueError("無法找到符合種子與抽籤限制的籤位。")
            summary = {
                "title": "建立主賽抽籤提案",
                "division_id": division_id,
                "output": draw["output"],
                "notice": "本次只建立提案；確認籤表後才會更新對戰。",
            }
        elif action == "approve_draw":
            draw = next(d for d in working.draws if d["id"] == operation["draw_id"])
            working, _ = approve(working, "draw:" + draw["id"], target_basis(draw), actor, reason)
            generate_matches(working)
            # Previous unstarted assignments with changed opponents need a new solve.
            affected = {m.id for m in working.matches if m.division_id == draw["division_id"]}
            if any(a.match_id in affected and any(a.locks.values()) for a in working.assignments):
                raise ValueError("此組有鎖定排程，請先由賽務確認鎖定。")
            working.assignments = [a for a in working.assignments if a.match_id not in affected]
            summary = {
                "title": "確認主賽籤表並更新對戰",
                "division_id": draw["division_id"],
                "output": draw["output"],
                "notice": "此組原排程已清除，下一步請重新排程。",
            }
        elif action == "results":
            audit = import_results(
                working, {"results": operation["results"], "actor_id": actor, "reason": reason}
            )
            summary = {"title": "確認比賽成績", "results": operation["results"]}
        elif action == "encouragement":
            proposal = operation["proposal"]
            working, audit = apply_draw(working, proposal, proposal["proposal_hash"], actor, reason)
            summary = {
                "title": "確認鼓勵賽抽籤",
                "output": proposal["output"],
                "notice": "套用後請重新排程。",
            }
        elif action == "manual_schedule":
            from .schedule_review import manual_draft

            working = manual_draft(snapshot, operation["updates"])
            summary = {"title": "預覽人工賽程調整"}
        elif action == "schedule":
            report = validate(working, "G3")
            if not report["passed"]:
                raise ValueError(
                    "對戰尚未通過檢查："
                    + "；".join(i["message"] for i in report["issues"] if i["severity"] == "error")[
                        :1800
                    ]
                )
            if "assignments" not in operation:
                from .operator_actions import schedule_live_options

                solved = solve_schedule(
                    working, time_limit_seconds=10, **schedule_live_options(working)
                )
                if solved["status"] not in {"OPTIMAL", "FEASIBLE"}:
                    raise ValueError("未找到可用排程：" + solved["status"])
                operation["assignments"] = [a.model_dump(mode="json") for a in working.assignments]
            else:
                working.assignments = [
                    Assignment.model_validate(a) for a in operation["assignments"]
                ]
                errors = [
                    i.message
                    for i in validate_schedule(working, baseline_assignments=snapshot.assignments)
                    if i.severity == "error"
                ]
                if errors:
                    raise ValueError("；".join(errors))
                from .operator_actions import validate_live_schedule

                live_errors = validate_live_schedule(working)
                if live_errors:
                    raise ValueError("；".join(live_errors))
            summary = {
                "title": "套用排程",
                "assignment_count": len(working.assignments),
                "changed_count": sum(a not in snapshot.assignments for a in working.assignments),
                "assignments": operation["assignments"],
            }
        else:
            raise ValueError("不支援的賽務操作。")
        if action in {"schedule", "manual_schedule", "local_replan"}:
            from .schedule_review import schedule_review

            review = schedule_review(snapshot, working)
            summary.update(
                schedule_review=review,
                can_apply=review["can_apply"],
                changed_count=review["changed_count"],
                assignment_count=review["assignment_count"],
            )
            if action == "manual_schedule" and review["changed_count"] == 0:
                summary.update(can_apply=False, notice="草案沒有變更任何場次，無須儲存。")
        working.status = "draft"
        return working, summary

    def preview(self, request: dict) -> dict:
        with self.guard:
            return self._preview(request)

    def _preview(self, request: dict) -> dict:
        snapshot = self.repo.load()
        if request.get("revision_id") != snapshot.id:
            raise Conflict("賽事已更新，請重新整理後再操作。")
        action = request.get("action")
        allowed = {
            "seeds": {"division_id", "mode", "seeds"},
            "draw": {"division_id", "seed"},
            "approve_draw": {"draw_id"},
            "results": {"results"},
            "encouragement": {"division_id", "seed"},
            "schedule": set(),
            "manual_schedule": {"updates"},
            "live_match": {"match_id", "event", "delay_minutes"},
            "reserve": {"entry_id"},
            "entry_change": {"entry_id", "kind", "replacement_entry_id"},
            "approve_entry_change": {"change_id"},
            "local_replan": {"change_id"},
            "restore_revision": {"target_revision_id"},
        }
        if action not in allowed:
            raise ValueError("不支援的賽務操作。")
        required = allowed[action] | {"action", "actor", "reason", "revision_id"}
        if set(request) != required:
            raise ValueError("操作資料欄位不完整或含不支援的欄位。")
        operation = deepcopy(request)
        if action in {
            "live_match",
            "reserve",
            "entry_change",
            "approve_entry_change",
            "local_replan",
        }:
            from .operator_actions import prepare_action

            operation = prepare_action(snapshot, operation)
        elif action == "restore_revision":
            from .operator_records import preview_restore_revision

            proposal = preview_restore_revision(
                self.repo,
                snapshot,
                operation["target_revision_id"],
                operation["actor"],
                operation["reason"],
            )
            operation["restore_proposal"] = proposal
            operation["restore_summary"] = {
                "title": "復原為新的賽事版本",
                "target_revision_id": operation["target_revision_id"],
                "target_created_at": proposal["target_created_at"],
                "target_actor": proposal["target_actor"],
                "target_reason": proposal["target_reason"],
                "diff": proposal["diff"],
                "notice": "保留所有歷史版本，建立新的草稿版本；不直接覆寫歷史紀錄。",
            }
        if action == "encouragement":
            operation["proposal"] = propose_draw(
                snapshot,
                operation["division_id"],
                operation["seed"],
                operation["actor"],
                operation["reason"],
            )
        _, summary = self._execute(snapshot, operation)
        identifier = new_id()
        plan = {
            "id": identifier,
            "base_revision_id": snapshot.id,
            "base_content_hash": content_hash(snapshot),
            "operation": operation,
            "summary": summary,
        }
        plan["hash"] = canonical_hash(plan)
        with self.guard:
            if len(self.plans) >= 100:
                self.plans.pop(next(iter(self.plans)))
            self.plans[identifier] = plan
        return deepcopy(
            {
                "id": identifier,
                "hash": plan["hash"],
                "summary": summary,
                "revision_id": snapshot.id,
            }
        )

    def confirm(self, request: dict) -> dict:
        with self.guard, self.repo.lock():
            plan = self.plans.get(request.get("id", ""))
            if plan is None or request.get("hash") != plan["hash"]:
                raise Conflict("此預覽已失效，請重新產生。")
            if plan["summary"].get("can_apply") is False:
                raise Conflict("草案仍有衝突或沒有可套用變更，請調整後重新預覽。")
            snapshot = self.repo.load()
            if (
                snapshot.id != plan["base_revision_id"]
                or content_hash(snapshot) != plan["base_content_hash"]
            ):
                raise Conflict("賽事已更新，原預覽不能套用，請重新整理。")
            working, summary = self._execute(snapshot, deepcopy(plan["operation"]))
            if summary != plan["summary"]:
                raise Conflict("預覽與目前計算結果不一致，請重新產生。")
            if summary.get("can_apply") is False:
                raise Conflict("草案尚未通過檢查，不能套用。")
            revision_id = new_id()
            working.changes.append(
                {
                    "id": new_id(),
                    "kind": "operator_action",
                    "actor_id": plan["operation"]["actor"],
                    "reason": plan["operation"]["reason"],
                    "base_revision_id": snapshot.id,
                    "created_at": utcnow().isoformat(),
                    "operation": plan["operation"]["action"],
                    "preview_hash": plan["hash"],
                    "before_hash": content_hash(snapshot),
                    "after_hash": content_hash(working),
                    **(
                        {
                            "schedule_review": deepcopy(summary["schedule_review"]),
                            "result_revision_id": revision_id,
                        }
                        if summary.get("schedule_review")
                        else {}
                    ),
                }
            )
            stored = self.repo.save(working, snapshot.id, revision_id=revision_id)
            del self.plans[plan["id"]]
            return {"revision_id": stored.id, "summary": summary}

    def export(self, revision_id: str) -> dict:
        with self.guard:
            return self._export(revision_id)

    def _export(self, revision_id: str) -> dict:
        from .operator_records import save_job

        repository = self.repo
        snapshot = repository.load()
        if snapshot.id != revision_id:
            raise Conflict("賽事已更新，請重新整理。")
        from .domain.encouragement_draws import validate_encouragement

        errors = validate_encouragement(snapshot)
        if errors:
            raise ValueError("；".join(i.message for i in errors))
        with self.guard:
            if any(j["status"] == "running" for j in self.jobs.values()):
                raise ValueError("已有文件正在產生，請稍候。")
            identifier = new_id()
            job: dict[str, Any] = {
                "id": identifier,
                "status": "running",
                "revision_id": snapshot.id,
                "created_at": utcnow().isoformat(),
                "directory": repository.root / "operator-exports" / identifier,
            }
            save_job(repository, job)
            self.jobs[identifier] = job

        def build():
            from .exporters.documents import build_documents

            try:
                manifest = build_documents(
                    snapshot, job["directory"], asset_root=repository.root / "assets"
                )
                if not manifest["qa"]["passed"]:
                    raise ValueError("文件品質檢查未通過。")
                with self.guard:
                    job.update(
                        status="completed",
                        url=f"/exports/{identifier}/index.html",
                        completed_at=utcnow().isoformat(),
                    )
                    save_job(repository, job)
            except Exception as exc:  # noqa: BLE001 - retain failure evidence for every background job
                with self.guard:
                    job.update(status="failed", error=str(exc))
                    save_job(repository, job)

        self.executor.submit(build)
        return {k: v for k, v in job.items() if k != "directory"}

    def workspaces(self) -> list[dict]:
        values = [
            {"id": "original", "label": "原工作區", "active": self.repo.root == self.base_repo.root}
        ]
        for path in sorted((self.base_repo.root / "operator-restores").glob("*.json")):
            try:
                identifier = str(UUID(path.stem))
                metadata = json.loads(path.read_text("utf-8"))
                target = self.base_repo.root / "restored-workspaces" / identifier
                if (
                    not target.is_dir()
                    or target.is_symlink()
                    or not target.resolve().is_relative_to(self.base_repo.root)
                ):
                    continue
                values.append(
                    {
                        "id": identifier,
                        "label": "還原工作區 " + identifier[:8],
                        "event_name": metadata.get("event_name", ""),
                        "active": self.repo.root == target.resolve(),
                    }
                )
            except (ValueError, OSError):
                continue
        return values

    def switch_workspace(self, request: dict) -> dict:
        from .operator_records import load_jobs

        with self.guard:
            snapshot = self.repo.load()
            if request.get("revision_id") != snapshot.id:
                raise Conflict("賽事已更新，請重新整理後再切換。")
            if not all(
                isinstance(request.get(key), str) and request[key].strip()
                for key in ("actor", "reason")
            ):
                raise ValueError("切換工作區須填寫操作者與原因。")
            if any(job["status"] == "running" for job in self.jobs.values()):
                raise ValueError("文件正在產生，完成後才能切換工作區。")
            identifier = request.get("restore_id")
            if identifier == "original":
                target = self.base_repo
            else:
                identifier = str(UUID(identifier))
                receipt = self.base_repo.root / "operator-restores" / (identifier + ".json")
                if not receipt.is_file():
                    raise ValueError("找不到已驗證的還原工作區。")
                target_path = self.base_repo.root / "restored-workspaces" / identifier
                if target_path.is_symlink() or not target_path.resolve().is_relative_to(
                    self.base_repo.root
                ):
                    raise ValueError("還原工作區路徑無效。")
                target = Repository(target_path)
            loaded = target.load()
            jobs = load_jobs(target)
            atomic_json(
                self.base_repo.root / "operator-workspace-events" / (new_id() + ".json"),
                {
                    "actor": request["actor"],
                    "reason": request["reason"],
                    "created_at": utcnow().isoformat(),
                    "target": str(identifier),
                    "revision_id": loaded.id,
                },
            )
            self.repo, self.jobs = target, jobs
            self.plans.clear()
            self.restore_plans.clear()
            self.history_cache = None
            return {
                "workspace_label": target.root.name,
                "revision_id": loaded.id,
                "notice": "已切換工作區；原工作區完整保留，未確認的操作預覽已失效。",
            }

    def backups(self) -> list[dict]:
        result = []
        for receipt in sorted((self.repo.root / "operator-backups").glob("*.json"), reverse=True):
            try:
                identifier = str(UUID(receipt.stem))
                archive = receipt.with_suffix(".zip")
                metadata = json.loads(receipt.read_text("utf-8"))
                if archive.is_file() and not archive.is_symlink():
                    result.append(
                        {
                            **metadata,
                            "id": identifier,
                            "url": f"/backups/{identifier}.zip",
                            "size_bytes": archive.stat().st_size,
                        }
                    )
            except (OSError, ValueError):
                continue
        return result

    def backup(self, request: dict) -> dict:
        from .operator_records import create_backup

        with self.guard, self.repo.lock():
            snapshot = self.repo.load()
            if request.get("revision_id") != snapshot.id:
                raise Conflict("賽事已更新，請重新整理後再備份。")
            actor = request.get("actor", "")
            if not isinstance(actor, str) or not actor.strip():
                raise ValueError("請填寫備份操作者。")
            archive = create_backup(self.repo)
            identifier = str(UUID(archive.stem))
            if archive.resolve().parent != (self.repo.root / "operator-backups").resolve():
                raise ValueError("備份產出路徑無效。")
            metadata = {
                "id": identifier,
                "created_at": utcnow().isoformat(),
                "actor": actor,
                "reason": request.get("reason") or "手動資料備份",
                "revision_id": snapshot.id,
                "size_bytes": archive.stat().st_size,
                "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            }
            atomic_json(archive.with_suffix(".json"), metadata)
            return {**metadata, "url": f"/backups/{identifier}.zip"}

    def backup_artifact(self, identifier: str) -> Path:
        identifier = str(UUID(identifier))
        with self.guard:
            directory = self.repo.root / "operator-backups"
            path = directory / (identifier + ".zip")
            metadata = json.loads(path.with_suffix(".json").read_text("utf-8"))
            if (
                path.is_symlink()
                or path.resolve().parent != directory.resolve()
                or hashlib.sha256(path.read_bytes()).hexdigest() != metadata.get("archive_sha256")
            ):
                raise ValueError("備份檔案已變更，無法下載。")
            return path

    def restore_preview(self, data: bytes, metadata: dict) -> dict:
        from .operator_records import inspect_backup

        with self.guard:
            snapshot = self.repo.load()
            if metadata.get("revision_id") != snapshot.id:
                raise Conflict("賽事已更新，請重新整理後再還原。")
            if not all(
                isinstance(metadata.get(k), str) and metadata[k].strip()
                for k in ("actor", "reason")
            ):
                raise ValueError("請填寫還原操作者與原因。")
            identifier = new_id()
            directory = self.base_repo.root / "operator-imports"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / (identifier + ".zip")
            try:
                path.write_bytes(data)
                inspected = inspect_backup(path)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            summary = {
                "title": "還原賽事資料到新工作區",
                **inspected,
                "notice": "建立新的還原工作區，不覆寫原工作區；匯出 PDF 與 Excel 不包含在此資料備份。",
            }
            plan = {
                "id": identifier,
                "base_revision_id": snapshot.id,
                "base_content_hash": content_hash(snapshot),
                "workspace": str(self.repo.root),
                "archive": str(path),
                "actor": metadata["actor"],
                "reason": metadata["reason"],
                "summary": summary,
                "archive_sha256": hashlib.sha256(data).hexdigest(),
            }
            plan["hash"] = canonical_hash(plan)
            if len(self.restore_plans) >= 10:
                old = self.restore_plans.pop(next(iter(self.restore_plans)))
                Path(old["archive"]).unlink(missing_ok=True)
            self.restore_plans[identifier] = plan
            return {"id": identifier, "hash": plan["hash"], "summary": summary}

    def restore_confirm(self, request: dict) -> dict:
        from .operator_records import restore_backup

        with self.guard, self.repo.lock():
            plan = self.restore_plans.get(request.get("id", ""))
            if not plan or request.get("hash") != plan["hash"]:
                raise Conflict("還原預覽已失效，請重新選擇備份。")
            current = self.repo.load()
            if (
                str(self.repo.root) != plan["workspace"]
                or current.id != plan["base_revision_id"]
                or content_hash(current) != plan["base_content_hash"]
            ):
                raise Conflict("賽事或工作區已更新，原還原預覽不能套用。")
            archive = Path(plan["archive"])
            if hashlib.sha256(archive.read_bytes()).hexdigest() != plan["archive_sha256"]:
                raise ValueError("上傳的備份檔已變更。")
            result = restore_backup(self.base_repo, archive, plan["actor"], plan["reason"])
            del self.restore_plans[plan["id"]]
            archive.unlink(missing_ok=True)
            return {
                **result,
                "workspace_label": "還原工作區 " + result["restore_id"][:8],
                "notice": "還原完成，原工作區未變更；可切換到還原工作區繼續操作。",
            }

    def artifact(self, identifier: str, relative: str) -> Path:
        with self.guard:
            job = self.jobs.get(identifier)
            if not job or job["status"] != "completed":
                raise ValueError("文件尚未完成或不存在。")
            root = Path(job["directory"]).resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("檔案不存在。")
        return path
