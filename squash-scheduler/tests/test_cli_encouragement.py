"""Real command boundaries preserve the repository until exact proposal confirmation."""

from pathlib import Path

from test_encouragement_draws import qualified

from squash_scheduler.cli import dispatch, parser
from squash_scheduler.domain.encouragement_draws import validate_encouragement
from squash_scheduler.storage.repository import Repository


def test_propose_review_confirm_and_replay_rejection(tmp_path):
    snapshot, division_id = qualified(3)
    repo = Repository(tmp_path / "tournament")
    with repo.lock():
        stored = repo.save(snapshot, None)

    def call(*command):
        args = parser().parse_args(
            [*command, "--workspace", str(repo.root), "--actor", "operator", "--json"]
        )
        return dispatch(args)

    proposal_path = tmp_path / "proposal.json"
    proposed, code = call(
        "encouragement",
        "propose",
        "--division",
        division_id,
        "--seed",
        "41",
        "--reason",
        "Review three eligible players",
        "--output",
        str(proposal_path),
    )
    assert code == 0 and repo.load().id == stored.id
    assert proposal_path.exists() and Path(proposed["review_html"]).exists()
    from pytest import raises

    with raises(ValueError, match="hash"):
        call(
            "encouragement",
            "apply",
            "--file",
            str(proposal_path),
            "--hash",
            "0" * 64,
            "--reason",
            "Confirm",
        )
    assert repo.load().id == stored.id
    applied, code = call(
        "encouragement",
        "apply",
        "--file",
        str(proposal_path),
        "--hash",
        proposed["proposal"]["proposal_hash"],
        "--reason",
        "Confirm reviewed draw",
    )
    assert code == 0 and applied["revision_id"] != stored.id
    assert len(applied["match_ids"]) == 2
    assert not validate_encouragement(repo.load())
    with raises(ValueError, match="STALE"):
        call(
            "encouragement",
            "apply",
            "--file",
            str(proposal_path),
            "--hash",
            proposed["proposal"]["proposal_hash"],
            "--reason",
            "Replay",
        )
    assert repo.load().id == applied["revision_id"]
