"""The launcher reports missing prerequisites without installing or editing an event."""

import importlib.util
import json
import socket
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "start_local", Path(__file__).resolve().parents[2] / "scripts/start_local.py"
)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_missing_uv_and_occupied_port_have_actionable_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        report = launcher.prerequisite_report(
            tmp_path, tmp_path / "new-event", occupied.getsockname()[1]
        )
    uv = next(item for item in report if item["key"] == "uv")
    port = next(item for item in report if item["key"] == "port")
    assert not uv["available"] and uv["required"]
    assert "安裝" in uv["message"]
    assert not port["available"] and "--port" in port["message"]
    assert not (tmp_path / "new-event").exists()


def test_check_mode_never_installs_or_launches(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        launcher.shutil, "which", lambda name: "/synthetic/uv" if name == "uv" else None
    )
    monkeypatch.setattr(
        launcher.subprocess,
        "call",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not start")),
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    workspace = tmp_path / "untouched"
    monkeypatch.setattr(
        launcher.sys,
        "argv",
        ["start_local.py", "--check", "--port", str(port), "--workspace", str(workspace)],
    )
    assert launcher.main() == 0
    assert "沒有安裝" in capsys.readouterr().out
    assert not workspace.exists()


def test_font_readiness_requires_the_configured_local_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    working = tmp_path / "working"
    working.mkdir()
    asset_id = "a" * 64
    (working / "current.json").write_text(
        json.dumps({"snapshot": {"config": {"publication": {"font_asset_id": asset_id}}}})
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert not next(
        row
        for row in launcher.prerequisite_report(tmp_path, tmp_path, port)
        if row["key"] == "font"
    )["available"]
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / (asset_id + ".bin")).write_bytes(b"synthetic-font")
    (assets / (asset_id + ".json")).write_text("{}")
    assert next(
        row
        for row in launcher.prerequisite_report(tmp_path, tmp_path, port)
        if row["key"] == "font"
    )["available"]


def test_python_prerequisite_distinguishes_an_old_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    executable = (
        tmp_path / ".venv" / ("Scripts/python.exe" if launcher.os.name == "nt" else "bin/python")
    )
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic executable")
    config = tmp_path / ".venv/pyvenv.cfg"
    config.write_text("version = 3.12.9\n")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    report = launcher.prerequisite_report(tmp_path, tmp_path / "event", port)
    assert not next(row for row in report if row["key"] == "python")["available"]
    config.write_text("version_info = 3.13.4\n")
    report = launcher.prerequisite_report(tmp_path, tmp_path / "event", port)
    assert next(row for row in report if row["key"] == "python")["available"]
