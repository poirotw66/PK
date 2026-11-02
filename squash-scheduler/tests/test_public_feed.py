"""Public projection privacy, reviewed advancement and revision-aware cache boundaries."""

import json

from test_operator import operator_snapshot
from test_rankings import result

from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.domain.models import new_id
from squash_scheduler.domain.rankings import import_results
from squash_scheduler.domain.seeds import configure_seeds
from squash_scheduler.public_feed import PublicFeed, PublicSnapshot, project_snapshot
from squash_scheduler.storage.repository import Repository


def fixture():
    snapshot, _ = operator_snapshot()
    generate_matches(snapshot)
    return snapshot


def test_public_contract_explicitly_excludes_private_records_and_nested_result_evidence():
    snapshot = fixture()
    marker = "PRIVATE-SOURCE-AND-OPERATOR-SECRET"
    snapshot.config["event"]["actor_id"] = marker
    snapshot.sources[0]["original_path"] = marker
    snapshot.participants[0].aliases.append(marker)
    snapshot.changes.append(
        {"id": new_id(), "kind": "operator_action", "reason": marker, "actor_id": marker}
    )
    first = next(m for m in snapshot.matches if m.round == 1)
    import_results(snapshot, {"actor_id": marker, "results": [result(first)]})
    snapshot.results[-1]["internal_note"] = marker
    public = project_snapshot(snapshot, "public-workspace").model_dump(mode="json")
    assert public["publication_status"] == "ready"
    assert marker not in json.dumps(public)
    assert (
        not {"sources", "changes", "actor", "history", "backups", "workspaces", "approvals"}
        & public.keys()
    )
    public_result = next(m for m in public["matches"] if m["id"] == first.id)["result"]
    assert set(public_result) == {"outcome", "winner_entry_id", "games"}
    assert public_result["games"] == [{"side_a": 11, "side_b": 4}] * 2
    PublicSnapshot.model_validate(public)


def test_only_confirmed_results_advance_and_corrections_update_downstream_name():
    snapshot = fixture()
    first = next(m for m in snapshot.matches if m.round == 1)
    target = next(
        m
        for m in snapshot.matches
        if any(getattr(s, "match_id", None) == first.id for s in (m.side_a, m.side_b))
    )
    key = "side_a" if getattr(target.side_a, "match_id", None) == first.id else "side_b"
    pending = result(first)
    pending["status"] = "pending"
    snapshot.results.append(pending)
    public = project_snapshot(snapshot, "scope")
    projected = next(m for m in public.matches if m.id == target.id)
    assert getattr(projected, key).entry_id is None
    assert getattr(projected, key).source_match_id == first.id
    snapshot.results.clear()
    import_results(snapshot, {"actor_id": "tester", "results": [result(first)]})
    projected = next(m for m in project_snapshot(snapshot, "scope").matches if m.id == target.id)
    assert getattr(projected, key).entry_id == first.side_a.entry_id
    import_results(
        snapshot,
        {
            "actor_id": "tester",
            "reason": "Corrected referee record",
            "results": [result(first, winner_a=False)],
        },
    )
    projected = next(m for m in project_snapshot(snapshot, "scope").matches if m.id == target.id)
    assert getattr(projected, key).entry_id == first.side_b.entry_id


def test_unapproved_redraw_does_not_publish_old_bracket_as_confirmed():
    snapshot = fixture()
    division = snapshot.divisions[0]
    entry = snapshot.entries[0]
    configure_seeds(
        snapshot,
        division.id,
        "provided",
        [{"entry_id": entry.id, "seed_number": 1, "allowed_positions": ["1"]}],
    )
    public = project_snapshot(snapshot, "scope")
    assert public.publication_status == "pending_review"
    assert not public.matches and not public.stages
    assert "待賽務確認" in public.notice


def test_version_poll_does_not_reload_or_project_unchanged_repository(tmp_path, monkeypatch):
    repository = Repository(tmp_path / "event")
    with repository.lock():
        saved = repository.save(fixture(), None)
    feed = PublicFeed()
    head = feed.version(repository)
    original = repository.load

    def forbidden(*args):
        raise AssertionError("unchanged version poll reloaded the snapshot")

    monkeypatch.setattr(repository, "load", forbidden)
    assert feed.version(repository) == head
    public = feed.read(repository)
    public["matches"].clear()
    assert feed.read(repository)["matches"]
    monkeypatch.setattr(repository, "load", original)
    with repository.lock():
        updated = repository.save(saved, saved.id)
    assert feed.version(repository)["revision_id"] == updated.id
    assert feed.version(repository)["version"] != head["version"]


def test_restored_child_with_same_revision_has_distinct_public_workspace_and_version(tmp_path):
    snapshot = fixture()
    one, two = Repository(tmp_path / "one"), Repository(tmp_path / "two")
    with one.lock():
        stored = one.save(snapshot, None)
    with two.lock():
        two.save(stored, None, revision_id=stored.id)
    feed = PublicFeed()
    before, after = feed.version(one), feed.version(two)
    assert before["revision_id"] == after["revision_id"]
    assert before["workspace_id"] != after["workspace_id"]
    assert before["version"] != after["version"]
    assert str(tmp_path) not in json.dumps(after)
