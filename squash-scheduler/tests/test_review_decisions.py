"""Review decisions bind exact memberships, ranking inputs, and persisted revisions."""

import json
from pathlib import Path

import pytest
from test_formats_draws import event

from squash_scheduler.domain import revisions
from squash_scheduler.domain.config import load_config
from squash_scheduler.domain.draws import run_draw
from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.domain.models import Entry, canonical_hash, content_hash, new_id
from squash_scheduler.domain.rankings import compute_rankings, import_results, ranking_basis
from squash_scheduler.domain.revisions import ReviewRequired, apply_patch, approve
from squash_scheduler.domain.validation import approval_valid, target_basis, validate
from squash_scheduler.storage.repository import Repository


def reviewed_event(count, template="round_robin"):
    snapshot, division = event(count, template)
    config_path = Path(__file__).resolve().parents[2] / "squash-event-config.template.json"
    snapshot.config = load_config(config_path)
    snapshot.config["divisions"] = [division.model_dump(mode="json")]
    snapshot.config["divisions_status"] = "configured"
    snapshot.config["draw_defaults"].update(
        seeded_entries_mode="none", affiliation_avoidance_mode="disabled"
    )
    snapshot.config["match_rule_defaults"].update(
        best_of_games=1, points_to_win_game=11, win_by_points=2, point_cap_mode="none"
    )
    source_id = new_id()
    snapshot.sources.append({"id": source_id, "sha256": "0" * 64})
    for registration in snapshot.registrations:
        registration.source_ref = {"source_file_id": source_id, "raw_text": "Synthetic player"}
    snapshot, _ = approve(
        snapshot,
        f"format:{division.id}",
        target_basis(division.format_plan),
        "reviewer",
        "Reviewed synthetic format",
    )
    return snapshot, snapshot.divisions[0]


def patch_for(snapshot, operations):
    return {
        "base_revision_id": snapshot.id,
        "actor_id": "reviewer",
        "reason": "Explicit organizer decision",
        "operations": operations,
    }


def count_issue(snapshot, code="DIVISION_TWO_ENTRIES"):
    return next(i for i in validate(snapshot, "G2")["issues"] if i["code"] == code)


def test_two_entry_treatment_needs_actual_approval_and_exact_membership():
    snapshot, division = reviewed_event(2)
    assert count_issue(snapshot)["status"] == "open"
    draft, _ = apply_patch(
        snapshot,
        patch_for(
            snapshot,
            [
                {
                    "op": "resolve_division_count",
                    "division_id": division.id,
                    "action": "direct_match",
                }
            ],
        ),
    )
    treatment = draft.divisions[0].rule_overrides["count_resolution"]
    assert treatment["entry_ids"] == sorted(e.id for e in draft.entries)
    assert count_issue(draft)["status"] == "open"
    approved, _ = approve(
        draft,
        f"division_count:{division.id}",
        target_basis(treatment),
        "reviewer",
        "Play an approved direct match",
    )
    assert count_issue(approved)["status"] == "resolved"
    assert validate(approved, "G2")["passed"]
    # Keep the same count and person while replacing the qualification's exact entry ID.
    prior = approved.entries[0]
    prior.status = "withdrawn"
    approved.entries.append(
        Entry(
            registration_id=prior.registration_id, effective_division_id=prior.effective_division_id
        )
    )
    assert count_issue(approved)["status"] == "open"
    before = approved.model_dump()
    with pytest.raises(ReviewRequired, match="inputs changed"):
        approve(
            approved,
            f"division_count:{division.id}",
            target_basis(treatment),
            "reviewer",
            "Cannot approve stale membership",
        )
    assert approved.model_dump() == before


def test_count_approval_cannot_be_forged_with_status_and_random_approval_id():
    snapshot, division = reviewed_event(2)
    draft, _ = apply_patch(
        snapshot,
        patch_for(
            snapshot,
            [
                {
                    "op": "resolve_division_count",
                    "division_id": division.id,
                    "action": "direct_match",
                }
            ],
        ),
    )
    treatment = draft.divisions[0].rule_overrides["count_resolution"]
    treatment.update(status="approved", approval_id=new_id())
    assert count_issue(draft)["status"] == "open"


@pytest.mark.parametrize("action", ["direct_match", "minimum_exception"])
def test_single_entry_division_cannot_be_risk_approved(action):
    snapshot, division = reviewed_event(1)
    before = snapshot.model_dump()
    with pytest.raises(ValueError, match="single-entry"):
        apply_patch(
            snapshot,
            patch_for(
                snapshot,
                [{"op": "resolve_division_count", "division_id": division.id, "action": action}],
            ),
        )
    assert snapshot.model_dump() == before
    assert count_issue(snapshot, "DIVISION_SINGLE_ENTRY")["status"] == "open"


def incomplete_ranking():
    snapshot, division = reviewed_event(3)
    generate_matches(snapshot)
    excluded_id = snapshot.matches[0].id
    snapshot, _ = apply_patch(
        snapshot, patch_for(snapshot, [{"op": "exclude_match", "match_id": excluded_id}])
    )
    exclusion = snapshot.exclusions[0]
    snapshot, _ = approve(
        snapshot,
        f"exclusion:{exclusion['id']}",
        target_basis(exclusion),
        "reviewer",
        "Approve an omitted round robin match",
    )
    generate_matches(snapshot)
    import_results(
        snapshot,
        {
            "actor_id": "scorer",
            "results": [normal_result(m) for m in snapshot.matches if m.status != "excluded"],
        },
    )
    stage = snapshot.stages[0]
    assert compute_rankings(snapshot, stage.id)["status"] == "manual_review"
    return snapshot, division, stage


def normal_result(match, winner_a=True):
    return {
        "match_id": match.id,
        "outcome": "played",
        "status": "confirmed",
        "winner_id": match.side_a.entry_id if winner_a else match.side_b.entry_id,
        "games": [{"side_a": 11, "side_b": 4} if winner_a else {"side_a": 4, "side_b": 11}],
    }


def ranking_patch(snapshot, stage_id, basis=None):
    return patch_for(
        snapshot,
        [
            {
                "op": "set_ranking_decision",
                "stage_id": stage_id,
                "ordered_entry_ids": sorted((e.id for e in snapshot.entries), reverse=True),
                "basis_hash": basis or ranking_basis(snapshot, stage_id),
            }
        ],
    )


def test_incomplete_ranking_becomes_final_only_after_content_bound_decision_approval():
    snapshot, _, stage = incomplete_ranking()
    draft, _ = apply_patch(snapshot, ranking_patch(snapshot, stage.id))
    decision = draft.ranking_decisions[-1]
    assert decision["status"] == "draft" and not decision["approval_id"]
    assert compute_rankings(draft, stage.id)["status"] == "manual_review"
    approved, _ = approve(
        draft,
        f"ranking:{decision['id']}",
        target_basis(decision),
        "reviewer",
        "Reviewed incomplete competition standings",
    )
    report = compute_rankings(approved, stage.id)
    assert report["status"] == "final"
    assert [row["entry_id"] for row in report["standings"]] == decision["ordered_entry_ids"]
    assert [row["rank"] for row in report["standings"]] == [1, 2, 3]
    assert approval_valid(approved, "ranking", decision["id"], approved.ranking_decisions[-1])


@pytest.mark.parametrize("phase", ["submit", "approve", "use"])
def test_changed_results_make_manual_ranking_basis_stale(phase):
    snapshot, _, stage = incomplete_ranking()
    original_basis = ranking_basis(snapshot, stage.id)
    if phase != "submit":
        snapshot, _ = apply_patch(snapshot, ranking_patch(snapshot, stage.id))
        decision = snapshot.ranking_decisions[-1]
        if phase == "use":
            snapshot, _ = approve(
                snapshot,
                f"ranking:{decision['id']}",
                target_basis(decision),
                "reviewer",
                "Reviewed standings",
            )
    match = next(m for m in snapshot.matches if m.status != "excluded")
    import_results(
        snapshot,
        {
            "actor_id": "scorer",
            "reason": "Correct the recorded winner",
            "results": [normal_result(match, winner_a=False)],
        },
    )
    assert ranking_basis(snapshot, stage.id) != original_basis
    before = snapshot.model_dump()
    if phase == "submit":
        with pytest.raises(ReviewRequired, match="stale"):
            apply_patch(snapshot, ranking_patch(snapshot, stage.id, original_basis))
    elif phase == "approve":
        with pytest.raises(ReviewRequired, match="inputs changed"):
            approve(
                snapshot,
                f"ranking:{decision['id']}",
                target_basis(decision),
                "reviewer",
                "Cannot approve changed results",
            )
    else:
        report = compute_rankings(snapshot, stage.id)
        assert report["status"] == "manual_review"
        assert any(i["code"] == "APPROVAL_STALE" for i in report["issues"])
    assert snapshot.model_dump() == before


def test_redraw_approval_supersedes_prior_run_and_stales_its_actual_approval():
    snapshot, division = reviewed_event(4, "single_elimination")
    first = run_draw(snapshot, division.id, 1, "draw-operator")
    snapshot, _ = approve(
        snapshot, f"draw:{first['id']}", target_basis(first), "reviewer", "Reviewed first draw"
    )
    second = run_draw(snapshot, division.id, 2, "draw-operator", reason="Approved redraw request")
    before = snapshot.model_dump()
    approved, _ = approve(
        snapshot,
        f"draw:{second['id']}",
        target_basis(second),
        "reviewer",
        "Reviewed replacement draw",
    )
    assert snapshot.model_dump() == before
    prior, current = approved.draws
    assert prior["status"] == "superseded" and current["status"] == "approved"
    old_action = next(a for a in approved.approvals if a["target_id"] == prior["id"])
    assert old_action["status"] == "stale"
    assert not approval_valid(approved, "draw", prior["id"], prior)
    assert approval_valid(approved, "draw", current["id"], current)


def test_failed_draw_cannot_be_approved_or_mutate_original():
    snapshot, division = reviewed_event(4, "single_elimination")
    affiliation = new_id()
    for participant in snapshot.participants:
        participant.affiliation_ids = [affiliation]
    division.draw_overrides = {
        "affiliation_avoidance_mode": "hard",
        "affiliation_avoidance_scope": "first_round",
        "max_attempts": 3,
    }
    failed = run_draw(snapshot, division.id, 3, "draw-operator")
    assert failed["output"] is None and failed["search_status"] == "not_found_within_limit"
    before = snapshot.model_dump()
    with pytest.raises(ReviewRequired, match="failed draw"):
        approve(
            snapshot,
            f"draw:{failed['id']}",
            target_basis(failed),
            "reviewer",
            "Cannot waive failed draw",
        )
    assert snapshot.model_dump() == before


def test_revision_approval_binds_reserved_id_report_hash_and_source_revision(tmp_path, monkeypatch):
    snapshot, _ = reviewed_event(3)
    repository = Repository(tmp_path / "tournament")
    with repository.lock():
        source = repository.save(snapshot, None)
    source_bytes = (repository.root / "revisions" / source.id / "snapshot.json").read_bytes()
    captured = []

    def valid_report(candidate, gate):
        captured.append(candidate.id)
        return {
            "revision_id": candidate.id,
            "content_hash": content_hash(candidate),
            "gate": gate,
            "passed": True,
            "warnings": 0,
            "errors": 0,
            "issues": [],
        }

    monkeypatch.setattr(revisions, "validate", valid_report)
    approved, outcome = approve(
        source, f"revision:{source.id}", content_hash(source), "reviewer", "Reviewed exact revision"
    )
    action, report = outcome["approval"], outcome["validation_report"]
    assert approved.id != source.id and captured == [approved.id]
    assert approved.status == "approved" and source.status != "approved"
    assert action["target_id"] == report["revision_id"] == approved.id
    assert action["source_revision_id"] == source.id
    assert action["basis_hash"] == report["content_hash"] == content_hash(approved)
    assert action["validation_report_hash"] == canonical_hash(report)
    with repository.lock():
        saved = repository.save(
            approved, source.id, revision_id=approved.id, report=report, approval=action
        )
    loaded = repository.load(saved.id)
    assert saved.id == approved.id == loaded.id == loaded.approvals[-1]["target_id"]
    assert loaded.parent_id == source.id
    saved_report = json.loads(
        (repository.root / "revisions" / saved.id / "validation-report.json").read_text("utf-8")
    )
    assert canonical_hash(saved_report) == action["validation_report_hash"]
    assert (
        repository.root / "revisions" / source.id / "snapshot.json"
    ).read_bytes() == source_bytes


@pytest.mark.parametrize("passed,warnings", [(False, 0), (True, 1)])
def test_revision_approval_cannot_waive_failed_gate_or_unresolved_warning(
    monkeypatch, passed, warnings
):
    snapshot, _ = reviewed_event(3)
    before = snapshot.model_dump()
    monkeypatch.setattr(revisions, "validate", lambda *_: {"passed": passed, "warnings": warnings})
    with pytest.raises(ReviewRequired, match="pass G5"):
        approve(
            snapshot,
            f"revision:{snapshot.id}",
            content_hash(snapshot),
            "reviewer",
            "Cannot waive gate",
        )
    assert snapshot.model_dump() == before
