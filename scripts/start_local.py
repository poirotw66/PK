"""Check prerequisites and start the local tournament interface."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path


def prerequisite_report(project: Path, workspace: Path, port: int) -> list[dict]:
    """Read local prerequisites without installing dependencies or altering data."""
    uv = shutil.which("uv")
    checks = [
        {
            "key": "uv",
            "required": True,
            "available": uv is not None,
            "message": f"uv 已找到：{uv}"
            if uv
            else "找不到 uv。請先安裝：https://docs.astral.sh/uv/，再重新啟動。",
        }
    ]
    local_python = (
        project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    version = ""
    try:
        values = dict(
            line.split("=", 1)
            for line in (project / ".venv/pyvenv.cfg").read_text("utf-8").splitlines()
            if "=" in line
        )
        version = next(
            (
                value.strip()
                for key, value in values.items()
                if key.strip() in {"version", "version_info"}
            ),
            "",
        )
    except OSError:
        pass
    python_ready = local_python.is_file() and version.startswith("3.13.")
    checks.append(
        {
            "key": "python",
            "required": False,
            "available": python_ready,
            "message": f"已有專案 Python {version} 環境；啟動時會依 uv.lock 檢查相依套件。"
            if python_ready
            else "尚未找到完整的專案 Python 3.13 環境。首次啟動可能需網路，以 uv.lock 建立鎖定環境；本次檢查不會安裝。",
        }
    )
    browser = next(
        (
            shutil.which(name)
            for name in (
                "msedge",
                "msedge.exe",
                "google-chrome",
                "chromium",
                "chromium-browser",
            )
            if shutil.which(name)
        ),
        None,
    )
    if browser is None:
        candidates = [
            Path(os.environ.get(key, "")) / "Microsoft/Edge/Application/msedge.exe"
            for key in ("PROGRAMFILES", "PROGRAMFILES(X86)")
            if os.environ.get(key)
        ]
        candidates += [
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
        browser = next((str(path) for path in candidates if path.is_file()), None)
    cache = Path(
        os.environ.get(
            "PLAYWRIGHT_BROWSERS_PATH",
            Path.home()
            / (
                "AppData/Local/ms-playwright"
                if os.name == "nt"
                else "Library/Caches/ms-playwright"
                if sys.platform == "darwin"
                else ".cache/ms-playwright"
            ),
        )
    )
    playwright = cache.is_dir() and any(
        path.is_dir() and path.name.startswith("chromium-") for path in cache.iterdir()
    )
    checks.append(
        {
            "key": "pdf_browser",
            "required": False,
            "available": bool(browser or playwright),
            "message": "已找到 PDF 瀏覽器候選；實際匯出仍會檢查啟動能力。"
            if browser or playwright
            else "尚未找到 PDF 瀏覽器。名單、排程仍可操作；產生 PDF 前請安裝 Edge，或在 squash-scheduler 執行 uv run python -m playwright install chromium。",
        }
    )
    current = workspace / "working/current.json"
    has_font = False
    if current.is_file():
        try:
            document = json.loads(current.read_text("utf-8"))
            asset_id = (
                document.get("snapshot", {})
                .get("config", {})
                .get("publication", {})
                .get("font_asset_id")
            )
            has_font = (
                isinstance(asset_id, str)
                and len(asset_id) == 64
                and all(character in "0123456789abcdef" for character in asset_id)
                and (workspace / "assets" / f"{asset_id}.bin").is_file()
                and (workspace / "assets" / f"{asset_id}.json").is_file()
            )
        except (OSError, ValueError, AttributeError):
            pass
    checks.append(
        {
            "key": "font",
            "required": False,
            "available": has_font,
            "message": "賽事已有字型資產；文件匯出仍會檢查中文字形及嵌入授權。"
            if has_font
            else "文件字型尚待核對。請到「建賽與雲端交換」匯入可嵌入的中文字型；系統不會以缺字字型產生正式 PDF。",
        }
    )
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
        available = True
    except OSError:
        available = False
    checks.append(
        {
            "key": "port",
            "required": True,
            "available": available,
            "message": f"本機連接埠 {port} 可使用。"
            if available
            else f"連接埠 {port} 無法使用，可能已有操作台執行。請先查看 http://127.0.0.1:{port}/，或指定 --port {port + 1 if port < 65535 else 8765}。",
        }
    )
    return checks


def main() -> int:
    project = Path(__file__).resolve().parents[1] / "squash-scheduler"
    parser = argparse.ArgumentParser(description="啟動本機賽事操作介面")
    parser.add_argument(
        "--workspace", type=Path, default=project / "tournaments" / "local-event"
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--check", action="store_true", help="只檢查本機條件，不安裝、不啟動或改動賽事"
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("連接埠須介於 1024 與 65535。")
    workspace = args.workspace.expanduser().resolve()
    print(f"賽事工作區：{workspace}", flush=True)
    checks = prerequisite_report(project, workspace, args.port)
    for check in checks:
        mark = (
            "可用" if check["available"] else "需處理" if check["required"] else "提示"
        )
        print(f"[{mark}] {check['message']}", flush=True)
    if any(check["required"] and not check["available"] for check in checks):
        return 1
    if args.check:
        print("本機必要條件檢查完成，沒有安裝套件或啟動服務。", flush=True)
        return 0
    print(
        f"啟動後開啟 http://127.0.0.1:{args.port}/；保留此視窗，按 Ctrl+C 停止。",
        flush=True,
    )
    uv = shutil.which("uv")
    assert uv is not None
    try:
        return subprocess.call(
            [
                uv,
                "run",
                "--frozen",
                "python",
                "-m",
                "squash_scheduler",
                "serve",
                "--workspace",
                str(workspace),
                "--port",
                str(args.port),
            ],
            cwd=project,
        )
    except KeyboardInterrupt:
        return 130
    except OSError as error:
        print(
            f"無法啟動操作台：{error}。請先執行 python3 scripts/start_local.py --check 核對環境。",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
