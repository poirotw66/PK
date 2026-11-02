"""Spectators use a credential-free read boundary; operator writes remain protected."""

# ruff: noqa: F811
import http.client
import json

import pytest
from test_operator_http import local_server, token  # noqa: F401 - shared loopback fixture
from test_rankings import result

from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.operator_server import create_server
from squash_scheduler.storage.repository import Repository


def request(port, path, *, headers=None, method="GET", payload=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    connection.request(
        method,
        path,
        body=json.dumps(payload) if payload is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    response = connection.getresponse()
    value = response.read()
    etag = response.getheader("ETag")
    connection.close()
    return response.status, value, etag


def ready(repo):
    snapshot = repo.load()
    generate_matches(snapshot)
    with repo.lock():
        return repo.save(snapshot, snapshot.id)


@pytest.mark.parametrize(
    "source",
    [
        {"type": "operator-state", "version_url": "/api/state"},
        {"type": "http-json", "version_url": "file:///private.json"},
        {"type": "http-json", "version_url": "https://user:secret@example.com/version.json"},
        {"type": "http-json", "version_url": "//example.com/version.json"},
        {"type": "http-json", "version_url": "https://example.com/\nunsafe"},
    ],
)
def test_reader_source_settings_reject_unsafe_or_operator_transports(tmp_path, source):
    with pytest.raises(ValueError):
        create_server(Repository(tmp_path / "event"), 0, reader_config={"source": source})


def test_public_page_and_data_have_no_operator_boot_or_write_access(local_server):
    port, repo = local_server
    ready(repo)
    for path in (
        "/live/",
        "/live.mjs",
        "/live-source.mjs",
        "/live-polygon.mjs",
        "/live.css",
        "/live-config.json",
    ):
        status, body, _ = request(port, path)
        assert status == 200, body
        assert b"__BOOT__" not in body and b"X-Squash-Token" not in body
    status, body, _ = request(port, "/public/snapshot.json")
    assert status == 200
    public = json.loads(body)
    assert public["publication_status"] == "ready" and public["matches"]
    assert str(repo.root) not in body.decode()
    assert not {"history", "backups", "actor", "workspaces", "jobs"} & public.keys()
    assert request(port, "/api/state")[0] == 403
    before = repo.load().id
    assert request(port, "/api/confirm", method="POST", payload={})[0] == 403
    assert request(port, "/public/snapshot.json", method="POST", payload={})[0] == 403
    assert repo.load().id == before


def test_preview_does_not_change_public_version_confirm_updates_advancement(local_server):
    port, repo = local_server
    snapshot = ready(repo)
    first = next(m for m in snapshot.matches if m.round == 1)
    target = next(
        m
        for m in snapshot.matches
        if any(getattr(side, "match_id", None) == first.id for side in (m.side_a, m.side_b))
    )
    status, body, etag = request(port, "/public/version.json")
    assert status == 200 and etag
    before = json.loads(body)
    assert request(port, "/public/version.json", headers={"If-None-Match": etag})[:2] == (304, b"")
    secret = token(port)
    headers = {"X-Squash-Token": secret}
    status, body, _ = request(
        port,
        "/api/preview",
        method="POST",
        headers=headers,
        payload={
            "action": "results",
            "results": [result(first)],
            "actor": "Justin",
            "reason": "Synthetic advancement",
            "revision_id": snapshot.id,
        },
    )
    assert status == 200, body
    plan = json.loads(body)
    assert request(port, "/public/version.json", headers={"If-None-Match": etag})[0] == 304
    assert (
        request(
            port,
            "/api/confirm",
            method="POST",
            headers=headers,
            payload={"id": plan["id"], "hash": plan["hash"]},
        )[0]
        == 200
    )
    status, body, changed_etag = request(
        port, "/public/version.json", headers={"If-None-Match": etag}
    )
    assert status == 200 and changed_etag != etag
    after = json.loads(body)
    assert after["version"] != before["version"]
    assert request(port, "/public/snapshot.json?version=" + before["version"])[0] == 409
    status, body, _ = request(port, "/public/" + after["snapshot_url"])
    assert status == 200
    public = json.loads(body)
    projected = next(m for m in public["matches"] if m["id"] == target.id)
    assert any(projected[key]["entry_id"] == first.side_a.entry_id for key in ("side_a", "side_b"))


def test_public_read_failure_does_not_disclose_private_filesystem_path(local_server, monkeypatch):
    port, repo = local_server

    def broken(*args):
        raise OSError("PRIVATE_PATH " + str(repo.root))

    monkeypatch.setattr(repo, "load", broken)
    status, body, _ = request(port, "/public/version.json")
    assert status == 503
    assert b"PRIVATE_PATH" not in body
    assert str(repo.root) not in body.decode()
