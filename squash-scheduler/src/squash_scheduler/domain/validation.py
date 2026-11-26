"""Operation-specific gates; draft loading never invents missing configuration."""

from __future__ import annotations

from typing import Any, Literal

from .config import EventConfig
from .models import Issue, Snapshot, canonical_hash, content_hash


def issue(
    code: str, message: str, gate: Literal["G1", "G2", "G3", "G4", "G5", "G6"] = "G1", **kwargs: Any
) -> Issue:
    return Issue(code=code, message=message, gate=gate, **kwargs)


def target_basis(value: dict) -> str:
    return canonical_hash({k: v for k, v in value.items() if k not in ("status", "approval_id")})


def approval_valid(snapshot: Snapshot, kind: str, target: str, value: dict) -> bool:
    return any(
        a.get("id") == value.get("approval_id")
        and a.get("target_type") == kind
        and a.get("target_id") == target
        and a.get("basis_hash") == target_basis(value)
        and a.get("status") != "stale"
        for a in snapshot.approvals
    )


def reviewed_entry_change(snapshot: Snapshot) -> dict[str, Any] | None:
    """Return only a current approved audit that proves its original reviewed graph."""
    from .withdrawals import entry_change_review_issues

    audit = next((c for c in reversed(snapshot.changes) if c.get("kind") == "entry_change"), None)
    if audit is None or audit.get("status") != "approved":
        return None
    if not approval_valid(snapshot, "entry_change", audit["id"], audit):
        return None
    if not any(
        a.get("id") == audit.get("approval_id") and a.get("action") == "approve"
        for a in snapshot.approvals
    ):
        return None
    return None if entry_change_review_issues(snapshot, audit) else audit


def original_entry_change_snapshot(audit: dict[str, Any]) -> Snapshot:
    """Find the original approvals through an already verified linear review chain."""
    before = Snapshot.model_validate(audit["before_snapshot"])
    while True:
        prior = next((c for c in reversed(before.changes) if c.get("kind") == "entry_change"), None)
        if prior is None:
            return before
        before = Snapshot.model_validate(prior["before_snapshot"])


def validate_references(snapshot: Snapshot) -> list[Issue]:
    issues: list[Issue] = []
    participants = {p.id: p for p in snapshot.participants}
    divisions = {d.id: d for d in snapshot.divisions}
    registrations = {r.id: r for r in snapshot.registrations}
    sources = {s["id"] for s in snapshot.sources}
    active: set[tuple[str, str]] = set()
    for participant in snapshot.participants:
        if participant.identity_status != "confirmed":
            issues.append(
                issue(
                    "IDENTITY_AMBIGUOUS",
                    "Participant identity requires confirmation",
                    entity_id=participant.id,
                )
            )
    for registration in snapshot.registrations:
        if (
            registration.participant_id not in participants
            or registration.original_division_id not in divisions
        ):
            issues.append(
                issue(
                    "REFERENCE_INVALID",
                    "Registration references a missing entity",
                    entity_id=registration.id,
                )
            )
        if registration.source_ref.get("source_file_id") not in sources:
            issues.append(
                issue(
                    "SOURCE_REFERENCE_MISSING",
                    "Registration source is not archived",
                    entity_id=registration.id,
                )
            )
        entries = [e for e in snapshot.entries if e.registration_id == registration.id]
        if sum(e.status == "active" for e in entries) > 1:
            issues.append(
                issue(
                    "DUPLICATE_ACTIVE_ENTRY",
                    "Registration has multiple active entries",
                    entity_id=registration.id,
                )
            )
        if registration.status != "accepted" and any(e.status == "active" for e in entries):
            issues.append(
                issue(
                    "REGISTRATION_UNACCOUNTED",
                    "Unaccepted registration has active entry",
                    entity_id=registration.id,
                )
            )
        if registration.status == "accepted" and not entries:
            issues.append(
                issue(
                    "REGISTRATION_UNACCOUNTED",
                    "Accepted registration has no disposition",
                    entity_id=registration.id,
                )
            )
        if registration.status == "historical_unresolved" and not snapshot.config.get(
            "historical_review", {}
        ).get("reconstruction_enabled", False):
            issues.append(
                issue(
                    "REGISTRATION_UNACCOUNTED",
                    "Historical exception is not valid for new events",
                    entity_id=registration.id,
                )
            )
    for entry in snapshot.entries:
        linked_registration = registrations.get(entry.registration_id)
        if not linked_registration or entry.effective_division_id not in divisions:
            issues.append(
                issue("REFERENCE_INVALID", "Entry references a missing entity", entity_id=entry.id)
            )
            continue
        if entry.status == "active":
            key = (linked_registration.participant_id, entry.effective_division_id)
            if key in active:
                issues.append(
                    issue(
                        "DUPLICATE_ACTIVE_ENTRY",
                        "Participant has duplicate active entries",
                        entity_id=entry.id,
                    )
                )
            active.add(key)
    return issues


def validate(snapshot: Snapshot, gate: str = "G4") -> dict[str, Any]:
    from .formats import validate_formats
    from .matches import validate_graph
    from .registrations import division_check

    if gate not in {f"G{i}" for i in range(1, 7)}:
        raise ValueError("Unknown validation gate")
    # Results and qualification do not depend on schedule/publication configuration.
    level = 3 if gate == "G6" else int(gate[1])
    entry_review = reviewed_entry_change(snapshot) if level >= 2 else None
    historical = original_entry_change_snapshot(entry_review) if entry_review else None
    issues = validate_references(snapshot)
    try:
        EventConfig.model_validate(snapshot.config)
    except (ValueError, KeyError) as exc:
        issues.append(issue("CONFIG_INVALID", str(exc)))
    issues.extend(
        i
        for i in snapshot.issues
        if i.status == "open" and (int(i.gate[1]) <= level or gate == "G6" and i.gate == "G6")
    )
    if level >= 2:
        checked = division_check(historical or snapshot)
        issues.extend(
            Issue.model_validate(i) if isinstance(i, dict) else i for i in checked.get("issues", [])
        )
        issues.extend(validate_formats(historical or snapshot))
        for classified_division in snapshot.divisions:
            if (
                classified_division.category is None or classified_division.gender_category is None
            ) and any(
                e.status == "active" and e.effective_division_id == classified_division.id
                for e in snapshot.entries
            ):
                issues.append(
                    issue(
                        "DIVISION_CLASSIFICATION_MISSING",
                        "Division category and gender category must be explicitly configured",
                        "G2",
                        entity_id=classified_division.id,
                    )
                )
        for exclusion in snapshot.exclusions:
            if exclusion.get("status") != "approved" or not approval_valid(
                snapshot, "exclusion", exclusion["id"], exclusion
            ):
                issues.append(
                    issue(
                        "MATCH_EXCLUSION_APPROVAL_REQUIRED",
                        "Match exclusion needs current approval",
                        "G2",
                        entity_id=exclusion["id"],
                    )
                )
        for division in snapshot.divisions:
            plan = division.format_plan
            if (
                plan
                and plan.get("status") == "approved"
                and not approval_valid(snapshot, "format", division.id, plan)
                and not (historical and approval_valid(historical, "format", division.id, plan))
            ):
                issues.append(
                    issue(
                        "APPROVAL_STALE",
                        "Format approval does not match current content",
                        "G2",
                        entity_id=division.id,
                    )
                )
        for entry_issue in issues:
            if entry_issue.code in ("DIVISION_TWO_ENTRIES", "DIVISION_BELOW_MINIMUM"):
                count_division = next(
                    (d for d in snapshot.divisions if d.id == entry_issue.entity_id), None
                )
                resolution = (
                    count_division.rule_overrides.get("count_resolution")
                    if count_division
                    else None
                )
                current_entries = sorted(
                    e.id
                    for e in snapshot.entries
                    if e.status == "active" and e.effective_division_id == entry_issue.entity_id
                )
                if (
                    resolution
                    and resolution.get("status") == "approved"
                    and resolution.get("entry_ids") == current_entries
                    and approval_valid(
                        snapshot, "division_count", entry_issue.entity_id or "", resolution
                    )
                ):
                    entry_issue.status = "resolved"
                    entry_issue.resolution_id = resolution["id"]
    if level >= 3:
        issues.extend(validate_graph(snapshot))
        # An approved redraw must never validate the graph generated from an older run.
        from .matches import generate_matches

        try:
            expected = (
                snapshot.model_copy(deep=True)
                if entry_review
                else generate_matches(snapshot.model_copy(deep=True))
            )

            def graph_payload(value: Snapshot) -> dict:
                return {
                    "matches": sorted(
                        (m.model_dump(mode="json") for m in value.matches), key=lambda m: m["id"]
                    ),
                    "pools": sorted(
                        (p.model_dump(mode="json") for p in value.pools), key=lambda p: p["id"]
                    ),
                }

            if canonical_hash(graph_payload(expected)) != canonical_hash(graph_payload(snapshot)):
                issues.append(
                    issue(
                        "MATCH_GRAPH_STALE",
                        "Match graph must be generated for the current format and draw",
                        "G3",
                    )
                )
        except ValueError as exc:
            issues.append(issue("MATCH_GRAPH_STALE", str(exc), "G3"))
        for graph_issue in issues:
            if graph_issue.code == "ROUND_ROBIN_INCOMPLETE":
                approved = [
                    e
                    for e in snapshot.exclusions
                    if e.get("match_id") == graph_issue.entity_id
                    and e.get("status") == "approved"
                    and approval_valid(snapshot, "exclusion", e["id"], e)
                ]
                if approved:
                    graph_issue.status = "resolved"
                    graph_issue.resolution_id = approved[0]["id"]
        for draw in snapshot.draws:
            if draw.get("status") == "superseded":
                continue
            if draw.get("kind") == "encouragement":
                # Validated with its current source evidence and complete graph by validate_graph.
                continue
            if draw.get("kind") == "deferred_slots":
                from .deferred_draws import validate_deferred_draw

                issues.extend(validate_deferred_draw(snapshot, draw))
                if draw.get("status") != "approved":
                    issues.append(
                        issue(
                            "DRAW_APPROVAL_REQUIRED",
                            "Deferred draw requires current approval",
                            "G3",
                            entity_id=draw["id"],
                        )
                    )
                continue
            if draw.get("status") != "approved" or not approval_valid(
                snapshot, "draw", draw["id"], draw
            ):
                if (
                    historical
                    and draw.get("status") == "approved"
                    and approval_valid(historical, "draw", draw["id"], draw)
                ):
                    continue
                issues.append(
                    issue(
                        "DRAW_APPROVAL_REQUIRED",
                        "Draw requires current approval",
                        "G3",
                        entity_id=draw["id"],
                    )
                )
    if level >= 4:
        from ..scheduling.constraints import validate_schedule

        issues.extend(validate_schedule(snapshot))
    if level >= 5:
        from ..exporters.prospectus import (
            PENDING,
            contains_demo_rule,
            publication_content_issues,
            rule_summary,
        )

        cfg = snapshot.config
        event = cfg.get("event", {})
        for field in ("name", "timezone", "event_dates", "venue_name"):
            if not event.get(field):
                issues.append(
                    issue(
                        "PUBLICATION_CONFIG_MISSING",
                        "Event publication field missing",
                        "G5",
                        field_path=f"event.{field}",
                    )
                )
        from .config import MatchRules
        from .match_rules import effective_match_rules

        required_fields = (
            "best_of_games",
            "points_to_win_game",
            "win_by_points",
            "late_arrival_rule",
            "walkover_rule",
            "retirement_rule",
            "double_forfeit_rule",
            "replacement_rule",
            "ranking_denominator_rule",
        )
        for match in snapshot.matches:
            if match.status in {"cancelled", "excluded"}:
                continue
            rules = (match.rule_snapshot or {}).get("match_rules") or effective_match_rules(snapshot, match)
            if contains_demo_rule(rules):
                issues.append(
                    issue(
                        "RULES_CONFIG_INVALID",
                        "Demonstration rules cannot be used for formal publication",
                        "G5",
                        entity_id=match.id,
                        field_path="rule_snapshot.match_rules",
                    )
                )
            if match.rule_snapshot is None:
                issues.append(
                    issue(
                        "RULE_SNAPSHOT_MISSING",
                        "Official match requires a fixed expanded rule snapshot",
                        "G5",
                        entity_id=match.id,
                    )
                )
            for field in required_fields:
                if rules.get(field) is None:
                    issues.append(
                        issue(
                            "RULES_CONFIG_MISSING",
                            "Effective official match rule must be explicit",
                            "G5",
                            entity_id=match.id,
                            field_path=f"rule_snapshot.match_rules.{field}",
                        )
                    )
            if rules.get("point_cap_mode") == "unconfigured":
                issues.append(
                    issue(
                        "RULES_CONFIG_MISSING",
                        "Point cap mode must be explicit",
                        "G5",
                        entity_id=match.id,
                    )
                )
            try:
                MatchRules.model_validate(
                    {k: v for k, v in rules.items() if k in MatchRules.model_fields}
                )
            except ValueError as exc:
                issues.append(issue("RULES_CONFIG_INVALID", str(exc), "G5", entity_id=match.id))
        pub = cfg.get("publication", {})
        if (
            pub.get("document_profile") == "prospectus"
            or pub.get("layout_template_id") == "reference-v2"
        ) and any(
            PENDING in line for summary in rule_summary(snapshot) for line in summary["lines"]
        ):
            issues.append(
                issue(
                    "RULES_CONFIG_MISSING",
                    "Public booklet rules contain unresolved terms",
                    "G5",
                    field_path="rule_snapshot.match_rules",
                )
            )
        for field_path in publication_content_issues(cfg):
            issues.append(
                issue(
                    "PUBLICATION_CONFIG_MISSING",
                    "Formal prospectus content must be complete and approved",
                    "G5",
                    field_path=field_path,
                )
            )
        for field in ("layout_template_id", "paper_size", "orientation", "font_asset_id"):
            if not pub.get(field):
                issues.append(
                    issue(
                        "PUBLICATION_CONFIG_MISSING",
                        "Publication setting missing",
                        "G5",
                        field_path=f"publication.{field}",
                    )
                )
        if pub.get("qr_mode") not in ("enabled", "disabled"):
            issues.append(issue("PUBLICATION_CONFIG_MISSING", "QR usage must be explicit", "G5"))
        if pub.get("qr_mode") == "enabled" and (
            not str(pub.get("qr_url", "")).startswith("https://") or not pub.get("qr_label")
        ):
            issues.append(
                issue("PUBLICATION_CONFIG_MISSING", "QR requires HTTPS URL and label", "G5")
            )
        if pub.get("sponsor_mode") not in ("provided", "none"):
            issues.append(
                issue("PUBLICATION_CONFIG_MISSING", "Sponsor usage must be explicit", "G5")
            )
        if pub.get("sponsor_mode") == "provided" and not pub.get("sponsors"):
            issues.append(issue("PUBLICATION_CONFIG_MISSING", "Sponsor content missing", "G5"))
    if gate == "G6":
        from .rankings import compute_rankings

        for stage in snapshot.stages:
            if stage.type == "round_robin":
                ranking = compute_rankings(snapshot, stage.id)
                issues.extend(Issue.model_validate(i) for i in ranking["issues"])
                if ranking["status"] == "provisional":
                    issues.append(
                        issue(
                            "RANKING_RESULTS_PENDING",
                            "Confirmed results are required for official qualification",
                            "G6",
                            entity_id=stage.id,
                        )
                    )
        for decision in snapshot.ranking_decisions:
            if decision.get("status") == "approved" and not approval_valid(
                snapshot, "ranking", decision["id"], decision
            ):
                issues.append(
                    issue(
                        "APPROVAL_STALE",
                        "Ranking approval does not match current decision",
                        "G6",
                        entity_id=decision["id"],
                    )
                )
    # Deterministic report hashes bind approvals to the exact diagnostics reviewed.
    unique = {canonical_hash(i): i.model_dump(mode="json") for i in issues}
    errors = [i for i in unique.values() if i["severity"] == "error" and i["status"] == "open"]
    warnings = [i for i in unique.values() if i["severity"] == "warning" and i["status"] == "open"]
    return {
        "schema_version": "1.0.0",
        "revision_id": snapshot.id,
        "content_hash": content_hash(snapshot),
        "gate": gate,
        "passed": not errors,
        "errors": len(errors),
        "warnings": len(warnings),
        "issues": list(unique.values()),
    }
