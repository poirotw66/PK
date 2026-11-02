"""Revision-bound cache and protected read-only workload HTTP endpoint."""

import json
import threading

import pytest
from test_operator import operator_snapshot
from test_operator_http import request, token

from squash_scheduler.domain.models import canonical_hash, content_hash
from squash_scheduler.operations import Conflict, OperatorService
from squash_scheduler.operator_server import create_server
from squash_scheduler.storage.repository import Repository


@pytest.fixture
def service(tmp_path):
    snapshot, _ = operator_snapshot()
    repo = Repository(tmp_path / "event")
    with repo.lock():
        repo.save(snapshot, None)
    result = OperatorService(repo)
    yield result
    result.close()


def test_cache_is_defensive_keyed_by_policy_revision_and_workspace(service, tmp_path, monkeypatch):
    calls = []

    def build(snapshot, *, policy=None):
        calls.append((snapshot.id, policy))
        return {"source_revision_id": snapshot.id, "participants": [], "policy": policy}

    monkeypatch.setattr("squash_scheduler.operator_workload.build_operator_workload", build)
    before = content_hash(service.repo.load())
    first = service.workload()
    first["participants"].append("client mutation")
    assert service.workload()["participants"] == [] and len(calls) == 1
    trial = service.workload("requested")
    assert trial["policy"] == {"minimum_rest_minutes": 60, "max_matches_per_day": 4}
    assert len(calls) == 2
    assert content_hash(service.repo.load()) == before
    assert first["workspace_id"] == service.state()["workspace_id"]
    prior = service.repo.load()
    with service.repo.lock():
        current = service.repo.save(prior, prior.id)
    with pytest.raises(Conflict, match="版本已更新"):
        service.workload(revision_id=prior.id)
    assert service.workload()["source_revision_id"] == current.id and len(calls) == 3
    child = Repository(tmp_path / "another-event")
    with child.lock():
        child.save(current, None, revision_id=current.id)
    service.repo = child
    other = service.workload()
    assert other["source_revision_id"] == current.id
    assert other["workspace_id"] != first["workspace_id"] and len(calls) == 4
    with pytest.raises(Conflict, match="工作區"):
        service.workload(workspace_id=first["workspace_id"])


def test_revision_change_during_analysis_is_not_cached_or_returned(service, monkeypatch):
    def build(snapshot, *, policy=None):
        with service.repo.lock():
            service.repo.save(snapshot, snapshot.id)
        return {"source_revision_id": snapshot.id}

    monkeypatch.setattr("squash_scheduler.operator_workload.build_operator_workload", build)
    with pytest.raises(Conflict, match="分析期間"):
        service.workload()
    assert not service.workload_cache


def test_workload_http_requires_token_and_rejects_stale_or_invalid_mode(service):
    server = create_server(service.repo, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    before = content_hash(service.repo.load())
    try:
        assert request(port, "GET", "/api/workload")[0] == 403
        headers = {"X-Squash-Token": token(port)}
        assert (
            request(
                port, "GET", "/api/workload", headers={**headers, "Origin": "https://evil.test"}
            )[0]
            == 403
        )
        assert request(port, "GET", "/api/workload?mode=unknown", headers=headers)[0] == 400
        assert request(port, "GET", "/api/workload?revision_id=stale", headers=headers)[0] == 409
        assert request(port, "GET", "/api/workload?workspace_id=other", headers=headers)[0] == 409
        code, body = request(port, "GET", "/api/workload?mode=requested", headers=headers)
        report = json.loads(body)
        assert code == 200 and report["mode"] == "simulation"
        assert report["workspace_id"] == canonical_hash({"workspace": str(service.repo.root)})[:24]
        assert report["source_revision_id"] == service.repo.load().id
        assert report["snapshot_content_hash"] == before
        assert content_hash(service.repo.load()) == before
        assert request(port, "GET", "/public/workload")[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
