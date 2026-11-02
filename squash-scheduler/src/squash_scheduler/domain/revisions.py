"""Content-bound approvals and all-or-nothing change requests."""

from __future__ import annotations

from typing import Any

from .config import EventConfig
from .models import Assignment, Snapshot, canonical_hash, content_hash, new_id, utcnow
from .validation import target_basis, validate


class ReviewRequired(ValueError):
    code = "REVIEW_REQUIRED"


def approve(
    snapshot: Snapshot, target: str, basis_hash: str, actor: str, reason: str
) -> tuple[Snapshot, dict]:
    if not actor.strip() or not reason.strip():
        raise ValueError("Approval requires actor and reason")
    working = snapshot.model_copy(deep=True)
    working.status = "draft"
    kind, target_id = target.split(":", 1)
    report = None
    if kind == "revision":
        if target_id != snapshot.id:
            raise ReviewRequired("Revision target is stale")
        actual = content_hash(snapshot)
        working.id = new_id()
        target_id = working.id
        report = validate(working, "G5")
        if not report["passed"] or report["warnings"]:
            raise ReviewRequired("Revision must pass G5 with all warnings dispositioned")
        working.status = "approved"
        value = None
    elif kind == "format":
        division = next(d for d in working.divisions if d.id == target_id)
        value = division.format_plan
        if not value:
            raise ValueError("Format plan is not configured")
        actual = target_basis(value)
    elif kind == "draw":
        from .draws import draw_inputs

        value = next(d for d in working.draws if d["id"] == target_id)
        if value.get("status") not in ("review_required", "draft"):
            raise ReviewRequired("Only a draft draw can be approved")
        if not value.get("output") or value.get("search_status") != "found":
            raise ReviewRequired("A failed draw cannot be approved")
        if value.get("input_hash") != canonical_hash(
            draw_inputs(working, value["division_id"], value["random_seed"])
        ):
            raise ReviewRequired("Draw inputs changed; run a reviewed redraw")
        actual = target_basis(value)
    elif kind == "deferred_draw":
        from .deferred_draws import validate_deferred_draw

        value = next(d for d in working.draws if d["id"] == target_id)
        if value.get("kind") != "deferred_slots" or value.get("status") != "draft":
            raise ReviewRequired("Only a draft deferred draw can be approved")
        errors = validate_deferred_draw(working, value)
        if errors:
            raise ReviewRequired("; ".join(item.message for item in errors))
        actual = target_basis(value)
    elif kind == "entry_change":
        from .withdrawals import entry_change_review_issues

        value = next(change for change in working.changes if change["id"] == target_id)
        if value.get("kind") != "entry_change" or value.get("status") != "draft":
            raise ReviewRequired("Only a draft entry change can be approved")
        entry_errors = entry_change_review_issues(working, value)
        if entry_errors:
            raise ReviewRequired("; ".join(entry_errors))
        actual = target_basis(value)
    elif kind == "exclusion":
        value = next(e for e in working.exclusions if e["id"] == target_id)
        if not value.get("reason"):
            raise ReviewRequired("Match exclusion requires specific reason")
        actual = target_basis(value)
    elif kind == "ranking":
        from .rankings import ranking_basis

        value = next(d for d in working.ranking_decisions if d["id"] == target_id)
        if value.get("basis_hash") != ranking_basis(working, value["stage_id"]):
            raise ReviewRequired("Ranking inputs changed; submit a new decision")
        actual = target_basis(value)
    elif kind == "division_count":
        division = next(d for d in working.divisions if d.id == target_id)
        value = division.rule_overrides.get("count_resolution")
        if not value:
            raise ReviewRequired("Count treatment is missing")
        entries = sorted(
            e.id
            for e in working.entries
            if e.effective_division_id == target_id and e.status == "active"
        )
        if entries != value.get("entry_ids") or division.minimum_entries != value.get(
            "minimum_entries"
        ):
            raise ReviewRequired("Count treatment inputs changed")
        actual = target_basis(value)
    else:
        raise ValueError("Unsupported approval target")
    if actual != basis_hash:
        raise ReviewRequired("Approval basis hash is stale")
    action: dict[str, Any] = {
        "id": new_id(),
        "actor_id": actor,
        "action": "approve",
        "target_type": kind,
        "target_id": target_id,
        "reason": reason,
        "created_at": utcnow().isoformat(),
        "basis_hash": actual,
    }
    if report:
        action["validation_report_hash"] = canonical_hash(report)
        action["source_revision_id"] = snapshot.id
    if kind == "draw" and value is not None:
        for prior in working.draws:
            if (
                prior["id"] != target_id
                and prior.get("division_id") == value.get("division_id")
                and prior.get("kind") == value.get("kind")
            ):
                prior["status"] = "superseded"
                for old_approval in working.approvals:
                    if old_approval.get("target_id") == prior["id"]:
                        old_approval["status"] = "stale"
    working.approvals.append(action)
    if value is not None:
        value["status"] = "approved"
        value["approval_id"] = action["id"]
    return working, {"approval": action, "validation_report": report}


def apply_patch(snapshot: Snapshot, patch: dict) -> tuple[Snapshot, dict]:
    from .registrations import transfer_entry

    if patch.get("base_revision_id") != snapshot.id:
        from ..storage.repository import RepositoryError

        raise RepositoryError("Patch base revision is stale")
    actor, reason = patch.get("actor_id"), patch.get("reason")
    if not actor or not reason:
        raise ValueError("Patch requires actor_id and reason")
    working = snapshot.model_copy(deep=True)
    working.status = "draft"
    changes = []
    for operation in patch.get("operations", []):
        op = operation["op"]
        if op == "set_config":
            field = operation["field"]
            allowed = {
                "event",
                "timing_defaults",
                "match_rule_defaults",
                "draw_defaults",
                "publication",
                "courts",
                "courts_status",
                "merge_whitelist",
                "merge_whitelist_status",
                "rule_sets",
            }
            if field not in allowed:
                raise ValueError("Configuration section is not editable through this operation")
            config = {**working.config, field: operation["value"]}
            working.config = EventConfig.model_validate(config).model_dump(mode="json")
            if field in {"timing_defaults", "match_rule_defaults", "rule_sets"}:
                from .rules import refresh_rule_snapshots

                refresh_rule_snapshots(working)
                for approval in working.approvals:
                    if approval.get("target_type") in {"revision", "schedule", "ranking"}:
                        approval["status"] = "stale"
        elif op == "set_stage_rules":
            stage = next(s for s in working.stages if s.id == operation["stage_id"])
            rule_id = operation["rule_set_id"]
            if rule_id is not None and rule_id not in working.config.get("rule_sets", {}):
                raise ValueError("Stage rule set does not exist")
            if any(
                m.status in {"in_progress", "completed"} and m.stage_id == stage.id
                for m in working.matches
            ):
                raise ReviewRequired(
                    "Started stage rules require a reviewed competition correction"
                )
            stage.rule_set_id = rule_id
            from .rules import refresh_rule_snapshots

            refresh_rule_snapshots(
                working, {m.id for m in working.matches if m.stage_id == stage.id}
            )
        elif op == "set_format":
            division = next(d for d in working.divisions if d.id == operation["division_id"])
            plan = dict(operation["value"])
            plan.update(status="draft", approval_id=None)
            division.format_plan = plan
        elif op == "resolve_division_count":
            division = next(d for d in working.divisions if d.id == operation["division_id"])
            entries = sorted(
                e.id
                for e in working.entries
                if e.status == "active" and e.effective_division_id == division.id
            )
            treatment = operation["action"]
            if not (
                (len(entries) == 2 and treatment == "direct_match")
                or (len(entries) >= 3 and treatment == "minimum_exception")
            ):
                raise ValueError(
                    "Count resolution is incompatible; single-entry divisions need an actual disposition"
                )
            division.rule_overrides["count_resolution"] = {
                "id": new_id(),
                "entry_ids": entries,
                "minimum_entries": division.minimum_entries,
                "action": treatment,
                "reason": reason,
                "status": "draft",
                "approval_id": None,
            }
        elif op == "set_ranking_decision":
            from .rankings import ranking_basis

            stage = next(s for s in working.stages if s.id == operation["stage_id"])
            if stage.type != "round_robin":
                raise ValueError("Manual rankings apply to a round-robin stage")
            stage_pools = [p for p in working.pools if p.stage_id == stage.id]
            pool_id = operation.get("pool_id")
            if stage_pools:
                members = next(p.entry_ids for p in stage_pools if p.id == pool_id)
            else:
                members = [
                    e.id
                    for e in working.entries
                    if e.effective_division_id == stage.division_id and e.status == "active"
                ]
            ordered = operation["ordered_entry_ids"]
            if len(ordered) != len(members) or set(ordered) != set(members):
                raise ValueError("Manual ranking must contain each group entry once")
            basis = ranking_basis(working, stage.id)
            if operation.get("basis_hash") != basis:
                raise ReviewRequired("Ranking basis hash is stale")
            working.ranking_decisions.append(
                {
                    "id": new_id(),
                    "stage_id": stage.id,
                    "pool_id": pool_id,
                    "ordered_entry_ids": ordered,
                    "basis_hash": basis,
                    "reason": reason,
                    "status": "draft",
                    "approval_id": None,
                }
            )
        elif op == "transfer_entry":
            transfer_entry(working, operation, actor, reason)
        elif op == "exclude_match":
            match = next(m for m in working.matches if m.id == operation["match_id"])
            if match.status in ("in_progress", "completed"):
                raise ReviewRequired("Started match cannot be excluded automatically")
            exclusion = {
                "id": new_id(),
                "match_id": match.id,
                "reason": reason,
                "status": "draft",
                "approval_id": None,
            }
            working.exclusions.append(exclusion)
            # Resource removal happens only after approval/regeneration.
        elif op == "withdraw_entry":
            entry = next(e for e in working.entries if e.id == operation["entry_id"])
            if any(m.status in ("in_progress", "completed") for m in working.matches):
                raise ReviewRequired("Post-start withdrawal requires explicit competition rules")
            if working.draws and operation.get("draw_action") not in ("redraw", "preserve_slot"):
                raise ReviewRequired("Choose preserve_slot or redraw explicitly")
            if working.draws:
                raise ReviewRequired("Post-draw withdrawal requires reviewed replacement/bye plan")
            entry.status = "withdrawn"
            for division in working.divisions:
                if division.id == entry.effective_division_id and division.format_plan:
                    division.format_plan.update(status="draft", approval_id=None)
        elif op == "set_lock":
            assignment = next(a for a in working.assignments if a.match_id == operation["match_id"])
            locks = operation["locks"]
            if set(locks) - {"time", "court", "sides", "draw"} or not all(
                isinstance(v, bool) for v in locks.values()
            ):
                raise ValueError("Unknown lock setting")
            assignment.locks = {**assignment.locks, **locks}
        elif op == "reschedule_match":
            match = next(m for m in working.matches if m.id == operation["match_id"])
            old = next(a for a in working.assignments if a.match_id == match.id)
            if match.status in ("in_progress", "completed"):
                raise ReviewRequired("Started match is immutable")
            new = Assignment.model_validate(
                {**old.model_dump(mode="json"), **operation["assignment"], "match_id": match.id}
            )
            if (
                old.locks.get("time")
                and old.start_at != new.start_at
                or old.locks.get("court")
                and old.court_id != new.court_id
            ):
                raise ReviewRequired("Change violates assignment lock")
            working.assignments = [
                new if a.match_id == match.id else a for a in working.assignments
            ]
            from ..scheduling.constraints import validate_schedule

            if any(
                i.severity == "error"
                for i in validate_schedule(working, baseline_assignments=snapshot.assignments)
            ):
                raise ValueError("Assignment violates schedule constraints")
        else:
            raise ValueError(f"Unsupported patch operation: {op}")
        changes.append(operation)
    change = {
        "id": new_id(),
        "base_revision_id": snapshot.id,
        "actor_id": actor,
        "reason": reason,
        "operations": changes,
        "created_at": utcnow().isoformat(),
        "before_hash": content_hash(snapshot),
        "after_hash": content_hash(working),
    }
    working.changes.append(change)
    return working, change
