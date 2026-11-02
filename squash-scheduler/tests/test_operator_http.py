"""Loopback UI rejects cross-origin and stale writes and never serves arbitrary files."""

import base64
import http.client
import json
import re
import threading

import pytest
from test_operator import operator_snapshot

from squash_scheduler.operator_server import create_server
from squash_scheduler.storage.repository import Repository


@pytest.fixture
def local_server(tmp_path):
    snapshot, _ = operator_snapshot()
    repo = Repository(tmp_path / "event")
    with repo.lock():
        repo.save(snapshot, None)
    server = create_server(repo, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port, repo
    server.shutdown()
    server.server_close()
    thread.join(5)


def request(port, method, path, payload=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    body = json.dumps(payload) if payload is not None else None
    connection.request(
        method, path, body=body, headers={"Content-Type": "application/json", **(headers or {})}
    )
    response = connection.getresponse()
    value = response.read()
    connection.close()
    return response.status, value


def token(port):
    status, html = request(port, "GET", "/")
    assert status == 200
    return json.loads(
        re.search(rb'<script id="boot" type="application/json">(.*?)</script>', html).group(1)
    )["token"]


def test_binary_backup_restore_preview_and_explicit_confirmation(local_server):
    port, repo = local_server
    secret = token(port)
    headers = {"X-Squash-Token": secret}
    before = repo.load().id
    metadata = {"revision_id": before, "actor": "Justin", "reason": "備份還原演練"}
    status, body = request(port, "POST", "/api/backup", metadata, headers)
    assert status == 200, body
    receipt = json.loads(body)
    status, archive = request(port, "GET", receipt["url"])
    assert status == 200 and archive.startswith(b"PK")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    connection.request(
        "POST",
        "/api/restore-preview",
        body=archive,
        headers={
            **headers,
            "Content-Type": "application/zip",
            "X-Restore-Metadata": base64.b64encode(json.dumps(metadata).encode()).decode(),
        },
    )
    response = connection.getresponse()
    plan = json.loads(response.read())
    connection.close()
    assert response.status == 200, plan
    assert repo.load().id == before
    status, body = request(
        port, "POST", "/api/restore-confirm", {"id": plan["id"], "hash": plan["hash"]}, headers
    )
    assert status == 200, body
    restored = json.loads(body)
    assert repo.load().id == before
    status, body = request(
        port,
        "POST",
        "/api/switch-workspace",
        {**metadata, "restore_id": restored["restore_id"]},
        headers,
    )
    assert status == 200, body
    status, body = request(port, "GET", "/api/state", headers=headers)
    assert status == 200
    assert any(
        w["active"] and w["id"] == restored["restore_id"] for w in json.loads(body)["workspaces"]
    )


def test_state_requires_token_and_rejects_wrong_host_and_origin(local_server):
    port, _ = local_server
    secret = token(port)
    assert request(port, "GET", "/api/state")[0] == 403
    assert request(port, "GET", "/api/state", headers={"X-Squash-Token": secret})[0] == 200
    assert (
        request(
            port,
            "GET",
            "/api/state",
            headers={"X-Squash-Token": secret, "Origin": "https://example.test"},
        )[0]
        == 403
    )
    assert request(port, "GET", "/", headers={"Host": "example.test"})[0] == 403
    navigation = {
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    }
    assert request(port, "GET", "/", headers=navigation)[0] == 200
    assert (
        request(port, "GET", "/api/state", headers={**navigation, "X-Squash-Token": secret})[0]
        == 403
    )


def test_http_preview_confirm_and_replay_are_atomic(local_server):
    port, repo = local_server
    secret = token(port)
    before = repo.load()
    headers = {"X-Squash-Token": secret}
    status, body = request(
        port,
        "POST",
        "/api/preview",
        {
            "action": "seeds",
            "division_id": before.divisions[0].id,
            "mode": "none",
            "seeds": [],
            "actor": "Justin",
            "reason": "Confirmed no seeds",
            "revision_id": before.id,
        },
        headers,
    )
    assert status == 200 and repo.load().id == before.id
    plan = json.loads(body)
    payload = {"id": plan["id"], "hash": plan["hash"]}
    assert request(port, "POST", "/api/confirm", payload, headers)[0] == 200
    assert repo.load().id != before.id
    assert request(port, "POST", "/api/confirm", payload, headers)[0] == 409


def test_unknown_artifacts_and_non_objects_are_rejected(local_server):
    port, repo = local_server
    original = repo.load().id
    assert request(port, "GET", "/exports/missing/../../working/current.json")[0] == 404
    assert request(port, "POST", "/api/preview", [], {"X-Squash-Token": token(port)})[0] == 400
    assert repo.load().id == original
