"""A warm start is never authority to commit an unverified or UNKNOWN schedule."""

from types import SimpleNamespace

from ortools.sat.python import cp_model
from test_scheduling import at, fixture

from squash_scheduler.domain.models import Assignment
from squash_scheduler.scheduling import solver
from squash_scheduler.scheduling.constraints import validate_schedule


def test_complete_valid_hint_does_not_turn_actual_unknown_into_success(monkeypatch):
    snapshot = fixture()
    original = snapshot.model_dump(mode="json")

    class UnknownSolver:
        def __init__(self):
            self.parameters = SimpleNamespace()
            self.wall_time = 0.1

        def solve(self, model):
            assert len(model.proto.solution_hint.vars) > 0
            return cp_model.UNKNOWN

        def status_name(self, _status):
            return "UNKNOWN"

    monkeypatch.setattr(solver.cp_model, "CpSolver", UnknownSolver)
    report = solver.solve_schedule(snapshot, time_limit_seconds=1)
    assert report["status"] == "UNKNOWN"
    assert report["diagnostics"]["warm_start"]["status"] == "validated_hint_only"
    assert not report["diagnostics"]["warm_start"]["committed_without_solver"]
    assert snapshot.model_dump(mode="json") == original


def test_invalid_court_overlap_hint_is_discarded_before_real_solver(monkeypatch):
    snapshot = fixture()
    court = snapshot.config["courts"][0]["id"]
    invalid = [
        Assignment(
            match_id=m.id,
            court_id=court,
            start_at=at("08:00"),
            play_end_at=at("08:25"),
            court_release_at=at("08:30"),
        )
        for m in snapshot.matches
    ]
    monkeypatch.setattr(solver, "_greedy_hint", lambda *_: invalid)
    report = solver.solve_schedule(snapshot, time_limit_seconds=5)
    assert report["status"] in {"FEASIBLE", "OPTIMAL"}
    assert report["diagnostics"]["warm_start"]["status"] == "rejected_by_independent_validator"
    assert not validate_schedule(snapshot)
