"""Immutable operator history, verifiable export receipts and portable data backups.

Call mutations while holding the source repository lock. Backups restore into a
new child repository; no uploaded path or persisted absolute export path is used.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import zipfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from .domain.models import Snapshot, canonical_hash, content_hash, new_id, utcnow
from .storage.repository import Repository, RepositoryError, atomic_json

_DATA_DIRS = {"working", "revisions", "source", "assets", "draws", "proposals", "operator-jobs"}
_MAX_FILES = 10000
_MAX_BYTES = 256 * 1024 * 1024
_MAX_ARCHIVE = 32 * 1024 * 1024
_MANIFEST = "backup-manifest.json"
_EXCLUDES = ["匯出文件（PDF、Excel、網站）", "既有備份", "其他還原工作區", "暫存上傳檔"]
_LABELS = {
    "config": "賽事設定",
    "participants": "選手資料",
    "registrations": "報名來源",
    "entries": "參賽資格",
    "divisions": "組別與種子",
    "stages": "賽程階段",
    "pools": "分組",
    "matches": "對戰籤表",
    "assignments": "場地與時間",
    "draws": "抽籤紀錄",
    "exclusions": "排除紀錄",
    "results": "比賽成績",
    "approvals": "核准紀錄",
    "changes": "操作紀錄",
    "sources": "來源檔案",
    "imports": "匯入紀錄",
    "ranking_decisions": "排名裁定",
    "issues": "檢核問題",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _event_name(snapshot: Snapshot) -> str:
    event = snapshot.config.get("event", {})
    return str(event.get("name", snapshot.config.get("event_name", "未命名賽事")))


_FIELDS = {
    **_LABELS,
    "status": "狀態",
    "name": "名稱",
    "label": "名稱",
    "official_name": "正式姓名",
    "display_name": "顯示姓名",
    "seeded_entries_mode": "種子模式",
    "seeded_entries": "種子名單",
    "seed_number": "種子序",
    "allowed_positions": "允許籤位",
    "draw_overrides": "抽籤設定",
    "draw_defaults": "預設抽籤設定",
    "format_plan": "賽制",
    "template_id": "賽制",
    "entry_id": "選手",
    "participant_id": "選手",
    "registration_id": "報名選手",
    "division_id": "組別",
    "effective_division_id": "參賽組別",
    "original_division_id": "原報名組別",
    "match_id": "場次",
    "court_id": "場地",
    "start_at": "開始",
    "play_end_at": "比賽結束",
    "court_release_at": "場地釋出",
    "locks": "鎖定",
    "time": "時間",
    "court": "場地",
    "side_a": "選手一",
    "side_b": "選手二",
    "round": "輪次",
    "display_code": "場號",
    "winner_id": "勝方",
    "games": "各局比分",
    "outcome": "判定",
    "reason": "原因",
    "actor_id": "操作者",
    "operation": "操作",
    "kind": "類型",
    "event": "事件",
    "requested_at": "事件時間",
    "delay_minutes": "延誤分鐘",
    "created_at": "記錄時間",
    "draw_id": "抽籤",
    "target_id": "對象",
    "type": "類型",
    "minimum_rest_minutes": "休息分鐘",
    "source_ref": "來源",
    "filename": "檔名",
    "file_name": "檔名",
    "path": "路徑",
    "rule_overrides": "競賽規則",
    "best_of_games": "最多局數",
    "minimum_entries": "最低人數",
    "timezone": "時區",
    "dates": "日期",
    "duration_minutes": "比賽分鐘",
}
_VALUES = {
    "active": "有效參賽",
    "accepted": "已接受",
    "withdrawn": "已退賽",
    "cancelled": "已取消",
    "transferred": "已轉組",
    "draft": "草稿",
    "approved": "已核准",
    "pending_review": "待審核",
    "review_required": "待審核",
    "confirmed": "已確認",
    "superseded": "已取代",
    "stale": "已失效",
    "planned": "待賽",
    "ready": "準備中",
    "in_progress": "比賽中",
    "completed": "已完成",
    "excluded": "已排除",
    "none": "無種子",
    "provided": "指定種子",
    "pending": "待處理",
    "played": "正常完賽",
    "walkover": "棄權",
    "no_show": "未到場",
    "retired": "退賽中止",
    "single_elimination": "單淘汰",
    "round_robin": "循環賽",
    "call": "叫號",
    "start": "開賽",
    "delay": "延誤",
    "clear_delay": "清除延誤",
    "reserve": "候補登錄",
    "seeds": "種子設定",
    "results": "成績登錄",
    "schedule": "排程",
    "restore_revision": "版本復原",
    "operator_action": "賽務操作",
    "live_match_event": "現場事件",
    "entry_change": "參賽異動",
}
_EVIDENCE = {
    "id",
    "preview_hash",
    "before_hash",
    "after_hash",
    "source_hash",
    "target_hash",
    "qualification_hash",
    "basis_hash",
    "input_hash",
    "sha256",
    "fingerprint",
    "base_revision_id",
    "source_revision_id",
    "target_revision_id",
    "superseded_change_ids",
}


def _names(snapshot: Snapshot | None) -> dict[str, str]:
    if snapshot is None:
        return {}
    names = {p.id: p.display_name for p in snapshot.participants}
    names.update({r.id: names.get(r.participant_id, "報名選手") for r in snapshot.registrations})
    names.update({e.id: names.get(e.registration_id, "參賽選手") for e in snapshot.entries})
    names.update({d.id: d.name for d in snapshot.divisions})
    names.update({s.id: s.label or s.stage_key or "賽程階段" for s in snapshot.stages})
    names.update(
        {
            m.id: m.display_code or f"第 {m.round} 輪／籤位 {m.bracket_position}"
            for m in snapshot.matches
        }
    )
    for court in snapshot.config.get("courts", []):
        if isinstance(court, dict) and isinstance(court.get("id"), str):
            names[court["id"]] = court.get("label", "場地")
    for draw in snapshot.draws:
        if draw.get("id"):
            names[draw["id"]] = names.get(str(draw.get("division_id", "")), "組別") + "抽籤"
    return names


def _readable(value: Any, names: dict[str, str], zone: str, depth: int = 0) -> str:
    if value is None:
        return "無"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, dict):
        if {"side_a", "side_b"} == value.keys():
            return f"{value['side_a']}:{value['side_b']}"
        parts = [
            f"{'／'.join(_FIELDS.get(p, p) for p in k.split('.'))}：{_readable(v, names, zone, depth + 1)}"
            for k, v in value.items()
            if k not in _EVIDENCE and not k.endswith("_hash")
        ]
        return (
            "；".join(parts[:6]) + (f"；另 {len(parts) - 6} 項" if len(parts) > 6 else "")
            or "已記錄"
        )
    if isinstance(value, list):
        if depth > 4:
            return f"{len(value)} 筆"
        items = [_readable(v, names, zone, depth + 1) for v in value[:6]]
        return "、".join(items) + (f"；另 {len(value) - 6} 筆" if len(value) > 6 else "") or "無"
    if isinstance(value, str):
        if value in names:
            return names[value]
        try:
            UUID(value)
            return "已記錄對象"
        except ValueError:
            pass
        if "T" in value:
            try:
                date = datetime.fromisoformat(value)
                if date.utcoffset() is not None:
                    return date.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
        return _VALUES.get(value, value)
    return str(value)


def _summary(value: Any, names: dict[str, str], zone: str) -> tuple[str, bool]:
    text = _readable(value, names, zone)
    return (text[:397] + "…", True) if len(text) > 400 else (text, False)


def _changed_fields(old: dict, new: dict) -> list[tuple[str, Any, Any]]:
    result: list[tuple[str, Any, Any]] = []
    for key in dict.fromkeys([*old, *new]):
        if key in _EVIDENCE or key.endswith("_hash") or old.get(key) == new.get(key):
            continue
        left, right = old.get(key), new.get(key)
        if isinstance(left, dict) and isinstance(right, dict):
            result.extend((key + "." + path, a, b) for path, a, b in _changed_fields(left, right))
        else:
            result.append((key, left, right))
    return result


def _diff(before: Snapshot | None, after: Snapshot) -> list[dict]:
    old = before.model_dump(mode="json") if before else {}
    new = after.model_dump(mode="json")
    old_names, new_names = _names(before), _names(after)
    # Resolve deleted objects through the other side without exposing raw identifiers.
    names = old_names | new_names
    zone = after.config.get("event", {}).get(
        "timezone", after.config.get("timezone", "Asia/Taipei")
    )
    result: list[dict] = []

    def add(label: str, path: str, left: Any, right: Any) -> None:
        a, truncated_a = _summary(left, names | old_names, zone)
        b, truncated_b = _summary(right, names | new_names, zone)
        result.append(
            {
                "label": label,
                "path": path,
                "before": a,
                "after": b,
                "truncated": truncated_a or truncated_b,
            }
        )

    for key, label in _LABELS.items():
        if old.get(key) == new.get(key):
            continue
        left, right = old.get(key), new.get(key)
        if key == "config":
            for path, a, b in _changed_fields(left or {}, right or {}):
                suffix = "／".join(_FIELDS.get(p, p) for p in path.split("."))
                add(f"{label} · {suffix}", key + "." + path, a, b)
            continue
        if isinstance(left, list) or isinstance(right, list):

            def index(items: list) -> dict[str, dict]:
                return {
                    str(item.get("id") or item.get("match_id") or canonical_hash(item)): item
                    for item in items
                    if isinstance(item, dict)
                }

            old_rows, new_rows = index(left or []), index(right or [])
            changed = [
                identity
                for identity in dict.fromkeys([*old_rows, *new_rows])
                if old_rows.get(identity) != new_rows.get(identity)
            ]
            for identity in changed[:20]:
                a, b = old_rows.get(identity), new_rows.get(identity)
                sample = b or a or {}
                entity = (
                    names.get(identity)
                    or names.get(str(sample.get("entry_id", "")))
                    or names.get(str(sample.get("match_id", "")))
                )
                row_label = f"{label} · {entity}" if entity else label
                if a is not None and b is not None:
                    fields = _changed_fields(a, b)
                    if fields:
                        add(
                            row_label,
                            key,
                            {path: x for path, x, _ in fields},
                            {path: y for path, _, y in fields},
                        )
                else:
                    add(row_label, key, a, b)
            if len(changed) > 20:
                add(
                    f"{label} · 其餘異動",
                    key,
                    f"原共 {len(left or [])} 筆",
                    f"新共 {len(right or [])} 筆，另 {len(changed) - 20} 筆異動可於資料備份查閱",
                )
            continue
        add(label, key, left, right)
    return result


def _restore_reason(snapshot: Snapshot) -> str:
    if snapshot.results or any(m.status in {"in_progress", "completed"} for m in snapshot.matches):
        return "已有開賽或成績，請使用成績更正及局部重排流程。"
    if any(any(a.locks.values()) for a in snapshot.assignments):
        return "已有鎖定賽程，請先依賽務流程處理鎖定。"
    return ""


def history(repo: Repository) -> list[dict]:
    """Return bounded human-readable differences; immutable files retain full evidence."""
    current = repo.load()
    current_reason = _restore_reason(current)
    rows = []
    for path in (repo.root / "revisions").glob("*/snapshot.json"):
        revision = repo.load(path.parent.name)
        parent = repo.load(revision.parent_id) if revision.parent_id else None
        old_changes = {canonical_hash(c) for c in parent.changes} if parent else set()
        added = [c for c in revision.changes if canonical_hash(c) not in old_changes]
        audit = next(
            (c for c in reversed(added) if c.get("kind") == "operator_action"),
            added[-1] if added else {},
        )
        reason = current_reason or _restore_reason(revision)
        if current.id == revision.id:
            reason = "目前版本"
        differences = _diff(parent, revision)
        rows.append(
            {
                "id": revision.id,
                "revision_id": revision.id,
                "parent_id": revision.parent_id,
                "created_at": revision.created_at.isoformat(),
                "actor": audit.get("actor_id", ""),
                "reason": audit.get("reason", ""),
                "action": audit.get("operation", audit.get("kind", "initial")),
                "diff": differences,
                "changes": differences,
                "changed_count": len(differences),
                "restorable": not reason,
                "can_restore": not reason,
                "blocked_reason": reason,
                "restore_reason": reason,
            }
        )
    return sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=True)


def preview_restore_revision(
    repo: Repository, current: Snapshot, target_revision_id: str, actor: str, reason: str
) -> dict:
    if (
        not isinstance(actor, str)
        or not actor.strip()
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ValueError("復原版本需要操作者與原因。")
    if current.id == target_revision_id:
        raise ValueError("目前已是此版本。")
    target = repo.load(target_revision_id)
    blocked = _restore_reason(current) or _restore_reason(target)
    if blocked:
        raise ValueError(blocked)
    target_audit = next(
        (c for c in reversed(target.changes) if c.get("kind") == "operator_action"),
        target.changes[-1] if target.changes else {},
    )
    return {
        "source_revision_id": current.id,
        "source_hash": canonical_hash(current.model_dump(mode="json")),
        "target_revision_id": target.id,
        "target_hash": canonical_hash(target.model_dump(mode="json")),
        "target_created_at": target.created_at.isoformat(),
        "target_actor": target_audit.get("actor_id", ""),
        "target_reason": target_audit.get("reason", ""),
        "actor": actor,
        "reason": reason,
        "diff": _diff(current, target),
    }


def execute_restore_revision(repo: Repository, current: Snapshot, proposal: dict) -> Snapshot:
    if (
        current.id != proposal["source_revision_id"]
        or canonical_hash(current.model_dump(mode="json")) != proposal["source_hash"]
    ):
        raise RepositoryError("版本已更新，請重新預覽復原。")
    target = repo.load(proposal["target_revision_id"])
    if canonical_hash(target.model_dump(mode="json")) != proposal["target_hash"]:
        raise RepositoryError("目標版本校驗失敗。")
    blocked = _restore_reason(current) or _restore_reason(target)
    if blocked:
        raise ValueError(blocked)
    working = target.model_copy(deep=True)
    working.id, working.parent_id, working.status = current.id, current.parent_id, "draft"
    working.changes.append(
        {
            "id": new_id(),
            "kind": "restore_revision",
            "actor_id": proposal["actor"],
            "reason": proposal["reason"],
            "created_at": utcnow().isoformat(),
            "source_revision_id": current.id,
            "source_hash": proposal["source_hash"],
            "target_revision_id": target.id,
            "target_hash": proposal["target_hash"],
            "superseded_change_ids": [c.get("id") for c in current.changes],
            "superseded_changes_hash": canonical_hash(current.changes),
        }
    )
    return working


def _job_directory(repo: Repository, identifier: str) -> Path:
    identifier = str(UUID(identifier))
    root = repo.root / "operator-exports"
    path = root / identifier
    if root.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(repo.root):
        raise ValueError("匯出路徑不合法。")
    return path


def save_job(repo: Repository, job: dict) -> None:
    identifier = str(UUID(job["id"]))
    _job_directory(repo, identifier)
    receipt = {k: v for k, v in job.items() if k not in {"directory", "url"}}
    atomic_json(repo.root / "operator-jobs" / (identifier + ".json"), receipt)


def _valid_export(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
    if manifest.get("qa", {}).get("passed") is not True:
        raise ValueError("匯出文件未通過檢查。")
    hashes = manifest.get("artifact_hashes", {})
    if not isinstance(hashes, dict) or not hashes or "index.html" not in hashes:
        raise ValueError("缺少匯出文件校驗紀錄。")
    for name, digest in hashes.items():
        path = directory / name
        if (
            not isinstance(name, str)
            or PurePosixPath(name).is_absolute()
            or "\\" in name
            or any(p in {"..", "."} for p in PurePosixPath(name).parts)
            or path.is_symlink()
            or not path.resolve().is_relative_to(directory.resolve())
            or not path.is_file()
            or _sha(path.read_bytes()) != digest
        ):
            raise ValueError("匯出文件校驗失敗。")
    return manifest


def load_jobs(repo: Repository) -> dict[str, dict]:
    """Recover receipt-backed jobs and verified older exports after a restart."""
    result = {}
    receipts = repo.root / "operator-jobs"
    candidates = {p.stem for p in receipts.glob("*.json")}
    candidates.update(p.name for p in (repo.root / "operator-exports").glob("*") if p.is_dir())
    for identifier in sorted(candidates):
        try:
            directory = _job_directory(repo, identifier)
            path = receipts / (identifier + ".json")
            if path.is_symlink():
                continue
            job = (
                json.loads(path.read_text("utf-8"))
                if path.exists()
                else {"id": identifier, "status": "completed"}
            )
            if not isinstance(job, dict) or job.get("id") != identifier:
                continue
            job = deepcopy(job)
            job["directory"] = str(directory)
            job.pop("url", None)
            if job.get("status") == "running":
                job.update(status="failed", error="上次匯出因程式結束而中斷，請重新匯出。")
                save_job(repo, job)
            elif job.get("status") == "completed":
                try:
                    manifest = _valid_export(directory)
                    if job.get("revision_id", manifest["revision_id"]) != manifest["revision_id"]:
                        raise ValueError("匯出版本與紀錄不一致。")
                    job.update(
                        revision_id=manifest["revision_id"],
                        created_at=job.get("created_at", manifest.get("created_at")),
                        url=f"/exports/{identifier}/index.html",
                    )
                except (ValueError, OSError, KeyError, TypeError):
                    if not path.exists():
                        continue
                    job.update(
                        status="failed",
                        error="匯出文件遺失或校驗失敗，請重新匯出；資料備份不含匯出文件。",
                    )
            elif job.get("status") != "failed":
                continue
            result[identifier] = job
        except (ValueError, OSError, KeyError, TypeError):
            # Corrupted or unverified output never becomes a downloadable success.
            continue
    return result


def _archive_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or ":" in name
        or "\x00" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or any(part.endswith((".", " ")) for part in path.parts)
        or any(
            part.split(".")[0].upper()
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *{f"COM{i}" for i in range(1, 10)},
                *{f"LPT{i}" for i in range(1, 10)},
            }
            for part in path.parts
        )
    ):
        raise ValueError("備份包含不安全的檔案路徑。")
    if path.parts[0] not in _DATA_DIRS and not (len(path.parts) == 1 and path.suffix == ".json"):
        raise ValueError("備份包含非賽事資料。")
    return path


def _backup_files(repo: Repository) -> list[Path]:
    files = []
    for path in repo.root.iterdir():
        if path.name in _DATA_DIRS:
            if path.is_symlink():
                raise ValueError("備份不接受連結目錄。")
            for child in path.rglob("*"):
                if child.is_symlink():
                    raise ValueError("備份不接受連結檔案。")
                if child.is_file():
                    files.append(child)
        elif path.is_file() and path.suffix == ".json" and not path.is_symlink():
            files.append(path)
    return sorted(files)


def create_backup(repo: Repository) -> Path:
    current = repo.load()
    files = _backup_files(repo)
    if len(files) > _MAX_FILES or sum(p.stat().st_size for p in files) > _MAX_BYTES:
        raise ValueError("賽事資料超過備份大小限制。")
    directory = repo.root / "operator-backups"
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / (new_id() + ".zip")
    hashes = {}
    try:
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as handle:
            for path in files:
                name = path.relative_to(repo.root).as_posix()
                _archive_name(name)
                raw = path.read_bytes()
                hashes[name] = {"sha256": _sha(raw), "size": len(raw)}
                handle.writestr(name, raw)
            manifest = {
                "schema_version": "1.0.0",
                "kind": "squash-event-data",
                "current_revision_id": current.id,
                "created_at": utcnow().isoformat(),
                "files": hashes,
                "excludes": _EXCLUDES,
            }
            handle.writestr(_MANIFEST, json.dumps(manifest, ensure_ascii=False))
        inspect_backup(archive)
    except BaseException:
        archive.unlink(missing_ok=True)
        raise
    return archive


def _read_backup(path: Path) -> tuple[dict, dict[str, bytes], Snapshot]:
    if path.stat().st_size > _MAX_ARCHIVE:
        raise ValueError("備份壓縮檔超過 32 MiB 限制。")
    try:
        with zipfile.ZipFile(path) as handle:
            infos = handle.infolist()
            if len(infos) > _MAX_FILES + 1 or sum(i.file_size for i in infos) > _MAX_BYTES:
                raise ValueError("備份解壓縮大小超過限制。")
            names = [i.filename for i in infos]
            if len(set(names)) != len(names) or len({n.casefold() for n in names}) != len(names):
                raise ValueError("備份有重複檔案。")
            for info in infos:
                if (
                    info.is_dir()
                    or stat.S_ISLNK(info.external_attr >> 16)
                    or info.flag_bits & 1
                    or info.file_size > _MAX_BYTES
                    or info.file_size > max(info.compress_size, 1) * 2000
                ):
                    raise ValueError("備份包含不允許的檔案類型或壓縮比例。")
                if info.filename != _MANIFEST:
                    _archive_name(info.filename)
            manifest = json.loads(handle.read(_MANIFEST))
            if (
                not isinstance(manifest, dict)
                or manifest.get("schema_version") != "1.0.0"
                or manifest.get("kind") != "squash-event-data"
            ):
                raise ValueError("不是支援的賽事備份格式。")
            expected = manifest.get("files")
            if not isinstance(expected, dict) or set(expected) != set(names) - {_MANIFEST}:
                raise ValueError("備份檔案清單不完整。")
            files = {}
            for name, evidence in expected.items():
                raw = handle.read(name)
                if (
                    not isinstance(evidence, dict)
                    or len(raw) != evidence.get("size")
                    or _sha(raw) != evidence.get("sha256")
                ):
                    raise ValueError("備份檔案校驗失敗。")
                files[name] = raw
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, TypeError, RuntimeError) as exc:
        raise ValueError("備份格式損毀或缺少校驗資料。") from exc
    current = _snapshot_bytes(files.get("working/current.json", b""))
    if current.id != manifest.get("current_revision_id"):
        raise ValueError("備份目前版本不一致。")
    expected_current = f"revisions/{current.id}/snapshot.json"
    if expected_current not in files or _snapshot_bytes(files[expected_current]) != current:
        raise ValueError("備份缺少目前版本的完整歷史。")
    snapshots = {}
    for name, raw in files.items():
        parts = PurePosixPath(name).parts
        if len(parts) == 3 and parts[0] == "revisions" and parts[2] == "snapshot.json":
            snapshot = _snapshot_bytes(raw)
            if str(UUID(parts[1])) != snapshot.id:
                raise ValueError("備份歷史版本編號不一致。")
            snapshots[snapshot.id] = snapshot
    for snapshot in snapshots.values():
        if snapshot.parent_id and snapshot.parent_id not in snapshots:
            raise ValueError("備份歷史版本鏈不完整。")
    checked = set()
    for identifier in snapshots:
        seen: set[str] = set()
        cursor: str | None = identifier
        while cursor and cursor not in checked:
            if cursor in seen:
                raise ValueError("備份歷史版本鏈有循環。")
            seen.add(cursor)
            cursor = snapshots[cursor].parent_id
        checked.update(seen)
    return manifest, files, current


def _snapshot_bytes(raw: bytes) -> Snapshot:
    try:
        envelope = json.loads(raw)
        if not isinstance(envelope, dict):
            raise TypeError("備份版本格式不完整。")
        snapshot = Snapshot.model_validate(envelope["snapshot"])
        if (
            canonical_hash(envelope["snapshot"]) != envelope["snapshot_hash"]
            or content_hash(snapshot) != envelope["content_hash"]
        ):
            raise ValueError("備份版本校驗失敗。")
        return snapshot
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("備份版本格式不完整。") from exc


def inspect_backup(path: Path) -> dict:
    _, files, current = _read_backup(path)
    return {
        "event_name": _event_name(current),
        "entries": len(current.entries),
        "matches": len(current.matches),
        "current_revision_id": current.id,
        "revision_count": sum(
            name.startswith("revisions/") and name.endswith("/snapshot.json") for name in files
        ),
        "files": len(files),
        "total_bytes": sum(map(len, files.values())),
        "archive_sha256": _sha(path.read_bytes()),
        "excludes": list(_EXCLUDES),
    }


def restore_backup(repo: Repository, archive: Path, actor: str, reason: str) -> dict:
    if (
        not isinstance(actor, str)
        or not actor.strip()
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ValueError("還原需要操作者與原因。")
    _, files, current = _read_backup(archive)
    identifier = new_id()
    parent = repo.root / "restored-workspaces"
    if parent.is_symlink():
        raise ValueError("還原目錄不可為連結。")
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / identifier
    target.mkdir()
    try:
        for name, raw in files.items():
            path = target.joinpath(*_archive_name(name).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as handle:
                handle.write(raw)
        restored = Repository(target)
        with restored.lock():
            restored.load()
            load_jobs(restored)  # Interrupted exports are explicit failures, never successes.
        result = {
            "workspace": str(target),
            "restore_id": identifier,
            "revision_id": current.id,
            "event_name": _event_name(current),
            "entries": len(current.entries),
            "matches": len(current.matches),
            "revision_count": sum(
                name.startswith("revisions/") and name.endswith("/snapshot.json") for name in files
            ),
            "actor": actor,
            "reason": reason,
            "created_at": utcnow().isoformat(),
            "archive_sha256": _sha(archive.read_bytes()),
        }
        atomic_json(repo.root / "operator-restores" / (identifier + ".json"), result)
        return result
    except BaseException:
        shutil.rmtree(target)
        raise
