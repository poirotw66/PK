"""The empty-workspace HTTP surface supports a safe file-to-event workflow."""

import http.client
import json
import threading

import pytest
from test_local_workflow import roster, settings
from test_operator_http import request, token

from squash_scheduler.operator_server import create_server
from squash_scheduler.storage.repository import Repository


@pytest.fixture
def empty_server(tmp_path):
    repo = Repository(tmp_path / "new")
    server = create_server(repo, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port, repo
    server.shutdown()
    server.server_close()
    thread.join(5)


def test_empty_workspace_serves_setup_and_protects_api(empty_server):
    port, repo = empty_server
    status, html = request(port, "GET", "/")
    assert status == 200 and "建賽與雲端交換".encode() in html
    assert request(port, "GET", "/setup.js")[0] == 200
    assert request(port, "GET", "/api/setup-state")[0] == 403
    secret = token(port)
    status, raw = request(port, "GET", "/api/setup-state", headers={"X-Squash-Token": secret})
    assert status == 200 and json.loads(raw)["initialized"] is False
    assert not (repo.root / "working/current.json").exists()


def test_http_upload_review_and_confirmation_initialize_once(empty_server):
    port, repo = empty_server
    secret = token(port)
    headers = {"X-Squash-Token": secret}
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    connection.request(
        "POST",
        "/api/intake?filename=synthetic.xlsx",
        body=roster(),
        headers={**headers, "Content-Type": "application/octet-stream"},
    )
    response = connection.getresponse()
    upload = json.loads(response.read())
    connection.close()
    assert response.status == 200, upload
    payload = {
        "actor": "synthetic-operator",
        "reason": "Synthetic HTTP workflow",
        "settings": settings(),
        "uploads": [upload["id"]],
        "rows": [
            {**r, "source_id": upload["id"], "identity": str(i)}
            for i, r in enumerate(upload["rows"])
        ],
    }
    status, raw = request(port, "POST", "/api/setup-preview", payload, headers)
    assert status == 200, raw
    plan = json.loads(raw)
    assert not (repo.root / "working/current.json").exists()
    status, raw = request(
        port, "POST", "/api/setup-confirm", {"id": plan["id"], "hash": plan["hash"]}, headers
    )
    assert status == 200, raw
    assert len(repo.load().entries) == 6
    assert request(port, "POST", "/api/setup-preview", payload, headers)[0] == 400
    status, html = request(port, "GET", "/")
    assert status == 200 and b"operator.js" in html
    assert request(port, "GET", "/operator")[0] == 200
    status, setup_html = request(port, "GET", "/setup")
    assert status == 200 and b'href="/operator#formats"' in setup_html


def test_upload_does_not_accept_cross_origin_or_unsupported_type(empty_server):
    port, repo = empty_server
    secret = token(port)
    headers = {"X-Squash-Token": secret, "Origin": "https://example.test"}
    assert request(port, "POST", "/api/intake", {}, headers)[0] == 403
    assert (
        request(port, "POST", "/api/intake?filename=bad.xlsx", {}, {"X-Squash-Token": secret})[0]
        == 400
    )
    assert not (repo.root / "working/current.json").exists()
