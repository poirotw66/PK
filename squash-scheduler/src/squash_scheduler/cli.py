"""Thin noninteractive CLI. Business rules live in the domain and application modules."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from .domain.config import load_config
from .domain.models import Division, Snapshot, content_hash, new_id, utcnow
from .domain.revisions import ReviewRequired, apply_patch, approve
from .domain.validation import target_basis, validate
from .storage.repository import Repository, RepositoryError, atomic_json


def read_json(path: str) -> dict:
    return json.loads(Path(path).read_text("utf-8-sig"))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="squash", description="壁球賽事草案、審核與文件工具")
    commands = root.add_subparsers(dest="group", required=True)

    def options(command: argparse.ArgumentParser) -> None:
        command.add_argument("--workspace", type=Path, required=True)
        command.add_argument("--actor")
        command.add_argument("--json", action="store_true")

    init = commands.add_parser("init")
    options(init)
    init.add_argument("--config", required=True)
    operator = commands.add_parser("serve")
    options(operator)
    operator.add_argument("--port", type=int, default=8765)
    operator.add_argument("--reader-config", type=Path, help="即時閱讀頁公開 JSON 資料來源設定")
    definitions = {
        "roster": ["import", "resolve"],
        "entries": ["list", "reserve", "propose", "apply"],
        "divisions": ["check"],
        "formats": ["recommend"],
        "changes": ["apply"],
        "draw": ["run"],
        "deferred-draw": ["list", "propose", "execute", "bind", "activate"],
        "matches": ["generate"],
        "schedule": ["solve", "replan", "audit"],
        "results": ["import"],
        "rankings": ["compute"],
        "encouragement": ["report", "propose", "apply"],
        "review": ["build", "approve"],
        "export": ["preview"],
        "release": ["build", "finalize"],
        "assets": ["import"],
        "revisions": ["diff"],
    }
    for group, actions in definitions.items():
        sub = commands.add_parser(group).add_subparsers(dest="action", required=True)
        for action in actions:
            command = sub.add_parser(action)
            options(command)
            if (group, action) in {
                ("roster", "import"),
                ("changes", "apply"),
                ("results", "import"),
                ("assets", "import"),
                ("release", "finalize"),
            }:
                command.add_argument("--file", required=True)
            if group == "assets":
                command.add_argument("--kind", choices=["font", "image"], required=True)
            if group == "entries":
                if action == "reserve":
                    command.add_argument("--entry", required=True)
                    command.add_argument("--reason", required=True)
                if action == "propose":
                    command.add_argument("--entry", required=True)
                    command.add_argument("--kind", choices=["withdraw", "replace"], required=True)
                    command.add_argument("--replacement")
                    command.add_argument("--reason", required=True)
                    command.add_argument("--output", type=Path)
                elif action == "apply":
                    command.add_argument("--file", required=True)
            if group == "deferred-draw" and action != "list":
                if action == "activate":
                    command.add_argument("--execution", required=True)
                else:
                    command.add_argument("--file", required=True)
                command.add_argument("--reason", required=True)
            if group == "roster" and action == "resolve":
                command.add_argument("--patch", required=True)
            if group == "changes":
                command.add_argument("--dry-run", action="store_true")
            if group == "draw":
                command.add_argument("--division", required=True)
                command.add_argument("--seed", required=True, type=int)
                command.add_argument("--reason")
            if group == "schedule":
                command.add_argument("--time-limit", type=float)
                command.add_argument("--minimum-rest", type=int)
                command.add_argument("--max-matches-per-day", type=int)
                if action == "audit":
                    command.add_argument("--output", type=Path)
                if action == "replan":
                    command.add_argument("--change", required=True)
            if group == "rankings":
                command.add_argument("--stage", required=True)
            if group == "encouragement":
                if action in {"report", "propose"}:
                    command.add_argument("--division", required=True)
                if action == "propose":
                    command.add_argument("--seed", type=int, required=True)
                    command.add_argument("--reason", required=True)
                    command.add_argument("--output", type=Path, required=True)
                if action == "apply":
                    command.add_argument("--file", required=True)
                    command.add_argument("--hash", required=True)
                    command.add_argument("--reason", required=True)
            if (group, action) == ("review", "approve"):
                command.add_argument("--target", required=True)
                command.add_argument("--hash", required=True)
                command.add_argument("--reason", required=True)
            if group == "release" and action == "build":
                command.add_argument("--revision", required=True)
            if group == "revisions":
                command.add_argument("--from", dest="from_id", required=True)
                command.add_argument("--to", dest="to_id", required=True)
    validation = commands.add_parser("validate")
    options(validation)
    validation.add_argument("--gate", choices=[f"G{i}" for i in range(1, 7)], default="G4")
    schema = commands.add_parser("schema")
    schema.add_argument("--output", type=Path, required=True)
    schema.add_argument("--json", action="store_true")
    return root


def require_actor(snapshot: Snapshot, override: str | None) -> str:
    actor = override or snapshot.config.get("event", {}).get("actor_id")
    if not actor or not actor.strip():
        raise ValueError("Mutation requires --actor or event.actor_id")
    return actor


def mutate(
    repo: Repository,
    args: argparse.Namespace,
    operation: Callable[[Snapshot, str], dict],
    *,
    save_failure: bool = False,
) -> tuple[dict, int]:
    with repo.lock():
        snapshot = repo.load()
        actor = require_actor(snapshot, args.actor)
        draft = snapshot.model_copy(deep=True)
        draft.status = "draft"
        result = operation(draft, actor)
        code = result.pop("_exit_code", 0)
        operations = result.pop("_operations", None)
        if code == 0 or save_failure:
            change = {
                "id": new_id(),
                "base_revision_id": snapshot.id,
                "actor_id": actor,
                "action": f"{args.group}.{args.action}",
                "created_at": utcnow().isoformat(),
                "before_hash": content_hash(snapshot),
                "after_hash": content_hash(draft),
            }
            if operations is not None:
                change["operations"] = operations
            draft.changes.append(change)
            stored = repo.save(draft, snapshot.id)
            result.update(revision_id=stored.id, content_hash=content_hash(stored))
            result["change_id"] = change["id"]
        return result, code


def dispatch(args: argparse.Namespace) -> tuple[dict, int]:
    if args.group == "serve":
        from .operator_server import serve

        serve(
            Repository(args.workspace),
            args.port,
            reader_config=read_json(str(args.reader_config)) if args.reader_config else None,
        )
        return {"status": "stopped"}, 0
    if args.group == "schema":
        from .domain.config import EventConfig
        from .public_feed import PublicSnapshot

        args.output.mkdir(parents=True, exist_ok=True)
        atomic_json(args.output / "snapshot.schema.json", Snapshot.model_json_schema())
        atomic_json(args.output / "event-config.schema.json", EventConfig.model_json_schema())
        atomic_json(args.output / "public-snapshot.schema.json", PublicSnapshot.model_json_schema())
        return {"output": str(args.output)}, 0
    repo = Repository(args.workspace)
    group, action = args.group, getattr(args, "action", None)
    if group == "init":
        config = load_config(Path(args.config))
        snapshot = Snapshot(
            config=config, divisions=[Division.model_validate(d) for d in config["divisions"]]
        )
        actor = require_actor(snapshot, args.actor)
        snapshot.config["event"]["actor_id"] = actor
        snapshot.config["event"]["id"] = snapshot.config["event"].get("id") or new_id()
        with repo.lock():
            if (repo.root / "working" / "current.json").exists():
                raise RepositoryError("Workspace is already initialized")
            snapshot = repo.save(snapshot, None)
        return {
            "revision_id": snapshot.id,
            "content_hash": content_hash(snapshot),
            "status": "draft",
            "missing_parameters": [k for k, v in config["event"].items() if v is None],
        }, 0
    if (group, action) == ("roster", "import"):
        from .importers.word import import_roster

        def roster_import(snapshot: Snapshot, actor: str) -> dict:
            report = import_roster(snapshot, Path(args.file))
            for source in snapshot.sources:
                if source["original_name"] == Path(args.file).name:
                    repo.archive_source(Path(args.file), source)
            atomic_json(repo.root / "import-report.json", report)
            if any(
                i["code"].startswith("IMPORT_") and i["severity"] == "error"
                for i in report["issues"]
            ):
                report["_exit_code"] = 7
            return report

        return mutate(repo, args, roster_import, save_failure=True)
    if (group, action) == ("assets", "import"):
        from .exporters.assets import import_asset

        with repo.lock():
            snapshot = repo.load()
            actor = require_actor(snapshot, args.actor)
            report = import_asset(repo.root, Path(args.file), args.kind)
            report["actor_id"] = actor
            return report, 0
    if (group, action) == ("roster", "resolve"):
        from .importers.word import resolve_roster

        def resolve(snapshot: Snapshot, actor: str) -> dict:
            patch = read_json(args.patch)
            if patch.get("base_revision_id") != snapshot.id:
                raise RepositoryError("Identity patch base revision is stale")
            if not patch.get("reason"):
                raise ValueError("Identity resolution requires reason")
            return resolve_roster(snapshot, {**patch, "actor_id": actor})

        return mutate(repo, args, resolve)
    if (group, action) == ("divisions", "check"):
        from .domain.registrations import division_check

        return division_check(repo.load()), 0
    if (group, action) == ("formats", "recommend"):
        from .domain.formats import recommend_formats

        return recommend_formats(repo.load()), 0
    if group == "entries":
        from .domain.withdrawals import apply_change, propose_change, register_reserve

        if action == "reserve":
            return mutate(
                repo,
                args,
                lambda s, actor: {
                    **register_reserve(s, args.entry, actor, args.reason),
                    "_operations": [
                        {"op": "register_reserve", "entry_id": args.entry, "reason": args.reason}
                    ],
                },
            )
        if action == "list":
            snapshot = repo.load()
            registrations = {r.id: r for r in snapshot.registrations}
            people = {p.id: p for p in snapshot.participants}
            divisions = {d.id: d for d in snapshot.divisions}
            return {
                "entries": [
                    {
                        "entry_id": e.id,
                        "name": people[
                            registrations[e.registration_id].participant_id
                        ].display_name,
                        "division": divisions[e.effective_division_id].name,
                        "status": e.status,
                        "participant_id": registrations[e.registration_id].participant_id,
                    }
                    for e in snapshot.entries
                ]
            }, 0
        with repo.lock():
            snapshot = repo.load()
            actor = require_actor(snapshot, args.actor)
            if action == "propose":
                proposal = propose_change(
                    snapshot, args.entry, args.kind, args.replacement, args.reason, actor
                )
                output = args.output or repo.root / "proposals" / (new_id() + ".json")
                atomic_json(output, proposal)
                return {"dry_run": True, "proposal": proposal, "file": str(output)}, 0
            proposal = read_json(args.file)
            if proposal.get("operation", {}).get("actor_id") != actor:
                raise ValueError("Entry change actor must match its reviewed proposal")
            working, audit = apply_change(snapshot, proposal)
            stored = repo.save(working, snapshot.id)
            return {
                "revision_id": stored.id,
                "change_id": audit["id"],
                "impact": audit["impact"],
                "approval_hash": target_basis(audit),
                "review_required": True,
            }, 0
    if group == "deferred-draw":
        from .domain.deferred_draws import (
            bind_deferred_draw,
            bind_deferred_execution,
            propose_deferred_draw,
            realize_deferred_draw,
        )

        if action == "list":
            snapshot = repo.load()
            return {
                "draws": [
                    record for record in snapshot.draws if record.get("kind") == "deferred_slots"
                ]
            }, 0

        def deferred(snapshot: Snapshot, actor: str) -> dict:
            if action in {"propose", "execute"}:
                operation = read_json(args.file)
                function = propose_deferred_draw if action == "propose" else realize_deferred_draw
                record = function(snapshot, operation, actor, args.reason)
                return {"draw": record, "approval_hash": target_basis(record)}
            if action == "bind":
                binding = read_json(args.file)
                affected = bind_deferred_draw(snapshot, binding["draw_run_id"], binding["bindings"])
            else:
                affected = bind_deferred_execution(snapshot, args.execution)
            return {
                "affected_match_ids": affected,
                "reason": args.reason,
                "_operations": [{"op": "bind_deferred_draw", "match_id": mid} for mid in affected],
            }

        return mutate(repo, args, deferred)
    if (group, action) == ("changes", "apply"):
        with repo.lock():
            snapshot = repo.load()
            actor = require_actor(snapshot, args.actor)
            patch = read_json(args.file)
            if patch.get("actor_id") != actor:
                raise ValueError("Patch actor must match CLI/configured actor")
            working, changes = apply_patch(snapshot, patch)
            if not args.dry_run:
                working = repo.save(working, snapshot.id)
            return {"dry_run": args.dry_run, "revision_id": working.id, "changes": changes}, 0
    if (group, action) == ("draw", "run"):
        from .domain.draws import run_draw

        def draw(snapshot: Snapshot, actor: str) -> dict:
            gate = validate(snapshot, "G2")
            if not gate["passed"]:
                raise ReviewRequired(
                    "Draw requires G2: " + ",".join(i["code"] for i in gate["issues"])
                )
            record = run_draw(snapshot, args.division, args.seed, actor, args.reason)
            atomic_json(repo.root / "draws" / (record["id"] + ".json"), record)
            atomic_json(repo.root / "draw-record.json", record)
            return {
                "draw": record,
                "approval_hash": target_basis(record),
                "_exit_code": 0 if record.get("output") else 4,
            }

        return mutate(repo, args, draw, save_failure=True)
    if (group, action) == ("matches", "generate"):
        from .domain.matches import dependency_map, generate_matches

        def matches(snapshot: Snapshot, actor: str) -> dict:
            if not validate(snapshot, "G2")["passed"]:
                raise ReviewRequired("Match generation requires G2")
            generate_matches(snapshot)
            used = {m.display_code for m in snapshot.matches if m.display_code}
            number, exhibition = 1, 1
            for match in snapshot.matches:
                if match.display_code:
                    continue
                if match.kind == "exhibition":
                    while f"EX-{exhibition:02}" in used:
                        exhibition += 1
                    match.display_code = f"EX-{exhibition:02}"
                else:
                    while str(number) in used:
                        number += 1
                    match.display_code = str(number)
                used.add(match.display_code)
            return {
                "matches": len(snapshot.matches),
                "dependencies": {k: sorted(v) for k, v in dependency_map(snapshot).items()},
            }

        return mutate(repo, args, matches)
    if group == "schedule":
        from .domain.config import PlayerWorkload
        from .scheduling.solver import solve_schedule

        def workload_policy(snapshot: Snapshot) -> dict:
            policy = dict(snapshot.config.get("player_workload") or {})
            if args.minimum_rest is not None:
                policy["minimum_rest_minutes"] = args.minimum_rest
            if args.max_matches_per_day is not None:
                policy["max_matches_per_day"] = args.max_matches_per_day
            return PlayerWorkload.model_validate(policy).model_dump(mode="json")

        if action == "audit":
            from .scheduling.workload import audit_workload

            snapshot = repo.load()
            report = audit_workload(snapshot, policy=workload_policy(snapshot))
            if args.output:
                atomic_json(args.output, report)
            return report, 0 if report["status"] == "passed" else 3

        def schedule(snapshot: Snapshot, actor: str) -> dict:
            if not validate(snapshot, "G3")["passed"]:
                raise ReviewRequired("Scheduling requires G3")
            if args.minimum_rest is not None or args.max_matches_per_day is not None:
                snapshot.config["player_workload"] = workload_policy(snapshot)
            local_ids = None
            if action == "replan":
                from .scheduling.diagnostics import impacted_match_ids

                change = next(c for c in snapshot.changes if c["id"] == args.change)
                seeds = {o["match_id"] for o in change["operations"] if "match_id" in o}
                seeds.update(change.get("impact", {}).get("affected_match_ids", []))
                if not seeds:
                    raise ValueError("Change has no explicit affected matches")
                local_ids = impacted_match_ids(snapshot, seeds)
            report = solve_schedule(
                snapshot, time_limit_seconds=args.time_limit, local_match_ids=local_ids
            )
            report["_exit_code"] = {"OPTIMAL": 0, "FEASIBLE": 0, "INFEASIBLE": 5, "UNKNOWN": 6}.get(
                report["status"], 3
            )
            atomic_json(repo.root / "solver-report.json", report)
            return report

        return mutate(repo, args, schedule)
    if (group, action) == ("results", "import"):
        from .domain.rankings import import_results

        return mutate(
            repo, args, lambda s, a: import_results(s, {**read_json(args.file), "actor_id": a})
        )
    if (group, action) == ("rankings", "compute"):
        from .domain.rankings import compute_rankings

        report = compute_rankings(repo.load(), args.stage)
        return report, 4 if report["status"] == "manual_review" else 0
    if group == "encouragement":
        from .domain.encouragement import eligibility_report
        from .domain.encouragement_draws import apply_draw, propose_draw

        if action == "report":
            return eligibility_report(repo.load(), args.division), 0
        if action == "propose":
            from .exporters.encouragement_proposal import render_proposal

            snapshot = repo.load()
            proposal = propose_draw(
                snapshot, args.division, args.seed, require_actor(snapshot, args.actor), args.reason
            )
            review_path = args.output.with_suffix(".review.html")
            if args.output == review_path or args.output.exists() or review_path.exists():
                raise ValueError(
                    "Choose a fresh proposal file; existing evidence cannot be overwritten"
                )
            atomic_json(args.output, proposal)
            review_path.write_text(render_proposal(proposal), "utf-8")
            return {
                "proposal": proposal,
                "file": str(args.output),
                "review_html": str(review_path),
            }, 0
        with repo.lock():
            snapshot = repo.load()
            updated, audit = apply_draw(
                snapshot,
                read_json(args.file),
                args.hash,
                require_actor(snapshot, args.actor),
                args.reason,
            )
            updated.changes.append(
                {
                    "id": new_id(),
                    "kind": "encouragement_apply",
                    "actor_id": require_actor(snapshot, args.actor),
                    "reason": args.reason,
                    "base_revision_id": snapshot.id,
                    "created_at": utcnow().isoformat(),
                    "impact": audit,
                }
            )
            stored = repo.save(updated, snapshot.id)
            return {**audit, "revision_id": stored.id, "content_hash": content_hash(stored)}, 0
    if group == "validate":
        report = validate(repo.load(), args.gate)
        atomic_json(repo.root / "validation-report.json", report)
        return report, 0 if report["passed"] else 3
    if (group, action) == ("review", "approve"):
        with repo.lock():
            snapshot = repo.load()
            actor = require_actor(snapshot, args.actor)
            working, approval = approve(snapshot, args.target, args.hash, actor, args.reason)
            working = repo.save(
                working,
                snapshot.id,
                report=approval["validation_report"],
                approval=approval["approval"],
                revision_id=working.id if working.status == "approved" else None,
            )
            return {**approval, "revision_id": working.id, "content_hash": content_hash(working)}, 0
    if (group, action) == ("review", "build"):
        from .exporters.documents import render_html, view_model

        snapshot = repo.load()
        report = validate(snapshot, "G4")
        model = view_model(snapshot)
        model["issues"] = report["issues"]
        folder = repo.root / "review"
        folder.mkdir(exist_ok=True)
        (folder / "review.html").write_text(
            render_html(model, folder, asset_root=repo.root / "assets"), "utf-8"
        )
        targets = [
            {"target": f"format:{d.id}", "hash": target_basis(d.format_plan)}
            for d in snapshot.divisions
            if d.format_plan
        ]
        targets += [
            {
                "target": ("deferred_draw:" if d.get("kind") == "deferred_slots" else "draw:")
                + d["id"],
                "hash": target_basis(d),
            }
            for d in snapshot.draws
            if d.get("kind") != "encouragement"
        ]
        targets += [
            {"target": "entry_change:" + change["id"], "hash": target_basis(change)}
            for change in snapshot.changes
            if change.get("kind") == "entry_change"
        ]
        targets += [
            {"target": f"exclusion:{e['id']}", "hash": target_basis(e)} for e in snapshot.exclusions
        ]
        targets += [
            {"target": f"ranking:{d['id']}", "hash": target_basis(d)}
            for d in snapshot.ranking_decisions
        ]
        targets += [
            {
                "target": f"division_count:{d.id}",
                "hash": target_basis(d.rule_overrides["count_resolution"]),
            }
            for d in snapshot.divisions
            if d.rule_overrides.get("count_resolution")
        ]
        targets.append({"target": f"revision:{snapshot.id}", "hash": content_hash(snapshot)})
        return {
            "review_html": str(folder / "review.html"),
            "validation": report,
            "approval_targets": targets,
        }, 0
    if (group, action) == ("export", "preview"):
        from .exporters.documents import build_documents

        snapshot = repo.load()
        destination = repo.root / "previews" / new_id()
        return {
            "path": str(destination),
            "manifest": build_documents(snapshot, destination, asset_root=repo.root / "assets"),
        }, 0
    if (group, action) == ("release", "build"):
        from .exporters.documents import release

        with repo.lock():
            require_actor(repo.load(), args.actor)
            report = release(repo, args.revision)
            return report, 4 if report["status"] == "qa_pending" else 0
    if (group, action) == ("release", "finalize"):
        from .exporters.releases import finalize_release

        with repo.lock():
            actor = require_actor(repo.load(), args.actor)
            return finalize_release(repo, read_json(args.file), actor), 0
    if (group, action) == ("revisions", "diff"):
        before, after = repo.load(args.from_id), repo.load(args.to_id)
        left, right = before.model_dump(mode="json"), after.model_dump(mode="json")
        changes = {
            key: {"before": left[key], "after": right[key]}
            for key in left
            if left[key] != right[key]
        }
        report = {"from": before.id, "to": after.id, "changes": changes}
        atomic_json(repo.root / "changes.json", report)
        return report, 0
    raise ValueError("Unsupported command")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        result, code = dispatch(args)
    except RepositoryError as exc:
        result, code = {"error": exc.code, "message": str(exc)}, 8
    except ReviewRequired as exc:
        result, code = {"error": exc.code, "message": str(exc)}, 4
    except (ValidationError, json.JSONDecodeError) as exc:
        result, code = {"error": "CONFIG_FORMAT_ERROR", "message": str(exc)}, 2
    except (ValueError, KeyError, StopIteration, TypeError) as exc:
        result, code = {"error": getattr(exc, "code", "VALIDATION_ERROR"), "message": str(exc)}, 3
    except (OSError, RuntimeError) as exc:
        result, code = {"error": "TOOL_FAILURE", "message": str(exc)}, 7
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return code
