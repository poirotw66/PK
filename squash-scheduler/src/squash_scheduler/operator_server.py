"""Loopback-only operator UI; stateful requests require an origin-bound session token."""

from __future__ import annotations

import base64
import json
import mimetypes
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, unquote, urlsplit

from .operations import Conflict, OperatorService
from .storage.repository import Repository, RepositoryError


def create_server(
    repository: Repository, port: int = 8765, *, reader_config: dict | None = None
) -> ThreadingHTTPServer:
    if reader_config is not None and not isinstance(reader_config, dict):
        raise TypeError("即時閱讀頁設定須為 JSON 物件。")
    config = reader_config or {
        "source": {"type": "http-json", "version_url": "/public/version.json"},
        "poll_interval_ms": 3000,
    }
    source = config.get("source", {})
    if not isinstance(source, dict):
        raise TypeError("即時閱讀頁來源設定須為 JSON 物件。")
    version_url = source.get("version_url", "")
    interval = config.get("poll_interval_ms", 3000)
    if (
        source.get("type") != "http-json"
        or not isinstance(version_url, str)
        or not version_url
        or any(c in version_url for c in "\r\n\t '\"<>")
        or type(interval) is not int
        or not 1000 <= interval <= 60000
    ):
        raise ValueError("即時閱讀頁來源設定無效。")
    parsed_source = urlsplit(version_url)
    source_origin = ""
    if parsed_source.netloc or parsed_source.scheme:
        if (
            parsed_source.scheme not in {"http", "https"}
            or not parsed_source.hostname
            or parsed_source.username
            or parsed_source.password
        ):
            raise ValueError("賽況來源須為不含帳密的 HTTP／HTTPS 網址。")
        source_origin = f"{parsed_source.scheme}://{parsed_source.netloc}"
    elif not version_url.startswith("/"):
        raise ValueError("本機賽況來源須使用以 / 開頭的路徑。")
    config = {
        "source": {"type": "http-json", "version_url": version_url},
        "poll_interval_ms": interval,
    }
    service = OperatorService(repository)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            pass

        def _reply(self, code: int, body: bytes, kind: str, *, etag: str | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            if code != 304:
                self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if etag:
                self.send_header("ETag", etag)
            self.send_header("X-Content-Type-Options", "nosniff")
            reader_origin = source_origin if urlsplit(self.path).path in {"/live", "/live/"} else ""
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; img-src 'self' data:; connect-src 'self' "
                + reader_origin
                + "; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, value: dict) -> None:
            self._reply(
                code,
                json.dumps(value, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _allowed(self, authenticated: bool = False) -> bool:
            origin = server_origin
            top_navigation = (
                not authenticated
                and self.command == "GET"
                and self.headers.get("Sec-Fetch-Mode") == "navigate"
                and self.headers.get("Sec-Fetch-Dest") == "document"
            )
            if (
                self.headers.get("Host") != origin.removeprefix("http://")
                or self.headers.get("Origin") not in {None, origin}
                or (
                    self.headers.get("Sec-Fetch-Site") not in {None, "none", "same-origin"}
                    and not top_navigation
                )
                or authenticated
                and not secrets.compare_digest(self.headers.get("X-Squash-Token", ""), token)
            ):
                self._json(403, {"error": "此操作僅接受本機賽務頁面。"})
                return False
            return True

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if not self._allowed(path.startswith("/api/")):
                return
            try:
                if path == "/":
                    html = (
                        files("squash_scheduler.assets")
                        .joinpath("operator.html")
                        .read_text("utf-8")
                    )
                    boot = json.dumps({"token": token}).replace("<", "\\u003c")
                    self._reply(
                        200,
                        html.replace("__BOOT__", boot).encode("utf-8"),
                        "text/html; charset=utf-8",
                    )
                elif path in {"/live", "/live/"}:
                    self._reply(
                        200,
                        files("squash_scheduler.assets").joinpath("live.html").read_bytes(),
                        "text/html; charset=utf-8",
                    )
                elif path in {"/live.mjs", "/live-source.mjs", "/live-polygon.mjs", "/live.css"}:
                    self._reply(
                        200,
                        files("squash_scheduler.assets").joinpath(path[1:]).read_bytes(),
                        ("text/css" if path.endswith(".css") else "text/javascript")
                        + "; charset=utf-8",
                    )
                elif path == "/live-config.json":
                    self._json(200, config)
                elif path in {"/public/version.json", "/public/snapshot.json"}:
                    payload = (
                        service.public_version()
                        if path.endswith("version.json")
                        else service.public_snapshot()
                    )
                    etag = '"' + payload["version"] + '"'
                    expected = parse_qs(urlsplit(self.path).query).get("version", [None])[0]
                    if expected is not None and expected != payload["version"]:
                        self._json(409, {"error": "賽況版本已更新，請重新取得最新版本。"})
                    elif self.headers.get("If-None-Match") == etag:
                        self._reply(304, b"", "application/json", etag=etag)
                    else:
                        self._reply(
                            200,
                            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                            "application/json; charset=utf-8",
                            etag=etag,
                        )
                elif path in {
                    "/operator.js",
                    "/schedule-ui.js",
                    "/draft-storage.js",
                    "/workload-ui.js",
                    "/format-ui.js",
                    "/operator.css",
                }:
                    data = files("squash_scheduler.assets").joinpath(path[1:]).read_bytes()
                    kind = "text/javascript" if path.endswith(".js") else "text/css"
                    self._reply(200, data, kind + "; charset=utf-8")
                elif path == "/font.woff2":
                    self._reply(
                        200,
                        files("squash_scheduler.assets")
                        .joinpath("fonts/IBMPlexSans.woff2")
                        .read_bytes(),
                        "font/woff2",
                    )
                elif path == "/api/state":
                    self._json(200, service.state())
                elif path == "/api/workload":
                    query = parse_qs(urlsplit(self.path).query)
                    self._json(
                        200,
                        service.workload(
                            query.get("mode", ["configured"])[0],
                            revision_id=query.get("revision_id", [None])[0],
                            workspace_id=query.get("workspace_id", [None])[0],
                        ),
                    )
                elif path.startswith("/exports/"):
                    parts = unquote(path).split("/", 3)
                    artifact = service.artifact(parts[2], parts[3])
                    self._reply(
                        200,
                        artifact.read_bytes(),
                        mimetypes.guess_type(artifact.name)[0] or "application/octet-stream",
                    )
                elif path.startswith("/backups/") and path.endswith(".zip"):
                    identifier = path.removeprefix("/backups/").removesuffix(".zip")
                    archive = service.backup_artifact(identifier)
                    self._reply(200, archive.read_bytes(), "application/zip")
                else:
                    self._json(404, {"error": "找不到此頁面。"})
            except (Conflict, RepositoryError) as exc:
                if path.startswith("/public/"):
                    self._json(503, {"error": "目前暫時無法取得賽況，請稍後重試。"})
                else:
                    self._json(409, {"error": str(exc)})
            except (ValueError, KeyError, IndexError, OSError) as exc:
                if path.startswith("/public/"):
                    self._json(503, {"error": "目前暫時無法取得賽況，請稍後重試。"})
                else:
                    self._json(400 if path == "/api/workload" else 404, {"error": str(exc)})
            except Exception:  # noqa: BLE001 - keep failed analysis structured and private
                self._json(500, {"error": "目前無法完成讀取或分析，請稍後重新整理。"})

        def do_POST(self) -> None:
            if not self._allowed(True):
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                path = urlsplit(self.path).path
                if path == "/api/restore-preview":
                    if (
                        not 0 < length <= 32 * 1024 * 1024
                        or self.headers.get_content_type() != "application/zip"
                    ):
                        raise ValueError("請上傳 32 MiB 以內的賽事 ZIP 備份。")
                    encoded = self.headers.get("X-Restore-Metadata", "")
                    if len(encoded) > 8192:
                        raise ValueError("還原操作資料過長。")
                    metadata = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
                    if not isinstance(metadata, dict):
                        raise TypeError("還原操作資料須為物件。")
                    self._json(200, service.restore_preview(self.rfile.read(length), metadata))
                    return
                if (
                    not 0 < length <= 262144
                    or self.headers.get_content_type() != "application/json"
                ):
                    self._json(400, {"error": "請以 JSON 傳送有效的賽務資料。"})
                    return
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise TypeError("操作資料須為物件。")
                if path == "/api/preview":
                    result = service.preview(request)
                elif path == "/api/confirm":
                    result = service.confirm(request)
                elif path == "/api/export":
                    result = service.export(request["revision_id"])
                elif path == "/api/backup":
                    result = service.backup(request)
                elif path == "/api/restore-confirm":
                    result = service.restore_confirm(request)
                elif path == "/api/switch-workspace":
                    result = service.switch_workspace(request)
                else:
                    self._json(404, {"error": "找不到此操作。"})
                    return
                self._json(200, result)
            except (Conflict, RepositoryError) as exc:
                self._json(409, {"error": str(exc)})
            except (ValueError, KeyError, StopIteration, TypeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception:  # noqa: BLE001 - HTTP boundary must always return a structured error
                self._json(500, {"error": "操作失敗，資料未完成儲存；請重新整理確認目前版本。"})

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def server_close(self) -> None:
            super().server_close()
            service.close()

    # Never accept a host override: this operator process is intentionally local.
    server = Server(("127.0.0.1", port), Handler)
    server_origin = f"http://127.0.0.1:{server.server_port}"
    return server


def serve(repository: Repository, port: int = 8765, *, reader_config: dict | None = None) -> None:
    repository.load()
    server = create_server(repository, port, reader_config=reader_config)
    print(f"本地賽務操作：http://127.0.0.1:{server.server_port}/", flush=True)
    print(f"即時閱讀頁：http://127.0.0.1:{server.server_port}/live/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
