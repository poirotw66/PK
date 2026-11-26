"""Local intake, explicit identity review and initial event setup."""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, time
from importlib.resources import files
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .domain.config import EventConfig
from .domain.models import (
    Division,
    Snapshot,
    canonical_hash,
    content_hash,
    new_id,
    stable_id,
    utcnow,
)
from .domain.registrations import resolve_roster
from .importers.intake import parse_intake

RULE_FIELDS = (
    "late_arrival_rule",
    "walkover_rule",
    "retirement_rule",
    "double_forfeit_rule",
    "replacement_rule",
    "ranking_denominator_rule",
    "multiway_tie_rule",
)


def rule_value(key: str, value: Any) -> dict | str | None:
    """Accept explicit outcome policies without guessing semantics from rule prose."""
    if value is None:
        return None
    if isinstance(value, str):
        if len(value) > 4000:
            raise ValueError("簡章條文須為 4,000 字以內文字。")
        return value.strip() or None
    if not isinstance(value, dict):
        raise ValueError("簡章條文須為文字或明確設定的特殊結果規則。")  # noqa: TRY004
    if key == "ranking_denominator_rule":
        if (
            not value
            or not set(value) <= {"played", "walkover", "retired", "double_forfeit", "cancelled"}
            or any(type(item) is not bool for item in value.values())
        ):
            raise ValueError("排名分母須逐項明確選擇計入或不計入。")
        return deepcopy(value)
    if key not in {"walkover_rule", "retirement_rule", "double_forfeit_rule"}:
        raise ValueError("此簡章條文須為文字，不能使用特殊結果設定。")
    points = (
        {"side_a_points", "side_b_points"}
        if key == "double_forfeit_rule"
        else {"winner_points", "loser_points"}
    )
    if set(value) != points | {"include_in_rankings", "counts_as_played"} or any(
        type(value.get(item)) is not bool for item in ("include_in_rankings", "counts_as_played")
    ):
        raise ValueError("特殊結果須明確指定排名、實際出賽與雙方積分。")
    if any(
        type(value[item]) not in {int, float} or not math.isfinite(value[item]) or value[item] < 0
        for item in points
    ):
        raise ValueError("特殊結果積分須為非負有限數字。")
    if key != "retirement_rule" and value["counts_as_played"]:
        raise ValueError("未出賽的棄權或雙方棄權不能列為實際出賽。")
    return deepcopy(value)


def identity_review(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[int]] = {}
    for index, row in enumerate(rows, 1):
        groups.setdefault(row["name"].strip(), []).append(index)
    return [
        {
            "name": name,
            "rows": indexes,
            "identities": len({rows[index - 1]["identity"] for index in indexes}),
            "divisions": sorted({rows[index - 1]["division"] for index in indexes}),
        }
        for name, indexes in groups.items()
        if len(indexes) > 1
    ]


def cloud_compatibility(settings: dict) -> dict:
    """Explain settings-level cloud limits before drawing or scheduling a match."""
    checks = []
    for key, expected, title in (
        ("rest", 60, "最低休息為 60 分鐘"),
        ("daily_limit", 5, "選手每日最多 5 場"),
        ("participant_scope", "confirmed_only", "僅檢查已確定選手"),
    ):
        checks.append(
            {
                "key": key,
                "status": "pass" if settings.get(key) == expected else "block",
                "message": title,
            }
        )
    window = settings.get("start", ""), settings.get("end", "")
    checks.append(
        {
            "key": "start_window",
            "status": "pass" if "08:00" <= window[0] < window[1] <= "17:30" else "warning",
            "message": "雲端只接受 08:00–17:30 的實際排定開賽時間；較寬的地端開放時段不會自動縮短。",
        }
    )
    compatible = not any(check["status"] == "block" for check in checks)
    return {
        "compatible": compatible,
        "checks": checks,
        "notice": "這是設定相容檢查，尚未代表賽制、籤表與排程已核准或可匯出。",
    }


def snapshot_cloud_compatibility(snapshot: Snapshot) -> dict:
    workload = snapshot.config.get("player_workload", {})
    timing = snapshot.config.get("timing_defaults", {})
    windows = [
        window
        for court in snapshot.config.get("courts", [])
        for window in court.get("availability", [])
    ]
    clocks = [
        (
            datetime.fromisoformat(window["start_at"])
            .astimezone(ZoneInfo("Asia/Taipei"))
            .strftime("%H:%M"),
            datetime.fromisoformat(window["end_at"])
            .astimezone(ZoneInfo("Asia/Taipei"))
            .strftime("%H:%M"),
        )
        for window in windows
    ]
    return cloud_compatibility(
        {
            "rest": workload.get("minimum_rest_minutes", timing.get("minimum_player_rest_minutes")),
            "daily_limit": workload.get("max_matches_per_day"),
            "participant_scope": workload.get("participant_scope"),
            "start": min((start for start, _ in clocks), default=""),
            "end": max((end for _, end in clocks), default=""),
        }
    )


def event_config(settings: dict, division_names: list[str], actor: str) -> dict:
    if not isinstance(settings, dict):
        raise TypeError("賽事設定須為物件。")
    if any(not isinstance(settings.get(k), str) for k in ("name", "venue")):
        raise ValueError("請填寫賽事名稱與場館。")
    config = json.loads(
        files("squash_scheduler.assets").joinpath("event-template.json").read_text("utf-8")
    )
    name, venue = settings.get("name", "").strip(), settings.get("venue", "").strip()
    if not name or not venue or len(name) > 200 or len(venue) > 200:
        raise ValueError("請填寫賽事名稱與場館（各 200 字以內）。")
    dates = settings.get("dates", [])
    if not isinstance(dates, list) or not 1 <= len(dates) <= 10 or len(set(dates)) != len(dates):
        raise ValueError("請設定 1 至 10 個不重複比賽日期。")
    dates = sorted(date.fromisoformat(day).isoformat() for day in dates)
    start, end = (
        time.fromisoformat(settings.get("start", "")),
        time.fromisoformat(settings.get("end", "")),
    )
    if start >= end or start.second or end.second:
        raise ValueError("每日開放結束時間須晚於開始時間，並使用整分鐘。")
    labels = settings.get("courts", [])
    if isinstance(labels, list) and all(isinstance(s, str) for s in labels):
        labels = [s.strip() for s in labels]
    if (
        not isinstance(labels, list)
        or not 1 <= len(labels) <= 20
        or any(not isinstance(s, str) or not s.strip() for s in labels)
        or len(set(labels)) != len(labels)
    ):
        raise ValueError("請填寫 1 至 20 個不重複場地名稱。")
    zone = ZoneInfo("Asia/Taipei")
    event_id = new_id()
    config["event"] = {
        "id": event_id,
        "name": name,
        "venue_name": venue,
        "timezone": "Asia/Taipei",
        "event_dates": dates,
        "actor_id": actor,
    }
    config["historical_review"] = {}
    config["divisions_status"] = "configured"
    classifications = settings.get("division_classifications", {})
    if not isinstance(classifications, dict) or any(
        not isinstance(classifications.get(name), dict)
        or classifications[name].get("category") not in {"competitive", "school"}
        or classifications[name].get("gender_category") not in {"male", "female", "mixed"}
        for name in division_names
    ):
        raise ValueError("請確認每個組別的競技／學校分類及性別分類。")
    config["divisions"] = [
        Division(
            id=stable_id(event_id, "division", name),
            name=name,
            status="active",
            minimum_entries=3,
            category=classifications[name]["category"],
            gender_category=classifications[name]["gender_category"],
        ).model_dump(mode="json")
        for name in division_names
    ]
    config["registration"]["division_aliases"] = {d["name"]: d["id"] for d in config["divisions"]}
    config["merge_whitelist_status"] = "configured"
    config["courts_status"] = "configured"
    config["courts"] = [
        {
            "id": stable_id(event_id, "court", str(i)),
            "label": label.strip(),
            "sort_order": i,
            "availability": [
                {
                    "start_at": datetime.combine(date.fromisoformat(day), start, zone).isoformat(),
                    "end_at": datetime.combine(date.fromisoformat(day), end, zone).isoformat(),
                }
                for day in dates
            ],
            "blackouts": [],
        }
        for i, label in enumerate(labels)
    ]
    numbers = {
        key: settings.get(key)
        for key in ("grid", "play", "buffer", "rest", "daily_limit", "transfer", "ranking_delay")
    }
    if any(type(value) is not int for value in numbers.values()):
        raise ValueError("時間、每日場數與格點須為整數。")
    config["timing_defaults"] = {
        "start_grid_minutes": numbers["grid"],
        "daily_grid_anchors": [
            {"date": d, "local_time": start.isoformat(timespec="minutes")} for d in dates
        ],
        "estimated_play_minutes": numbers["play"],
        "court_buffer_minutes": numbers["buffer"],
        "minimum_player_rest_minutes": numbers["rest"],
        "court_transfer_minutes": numbers["transfer"],
        "ranking_confirmation_minutes": numbers["ranking_delay"],
    }
    scope = settings.get("participant_scope")
    if scope not in {"all_possible", "confirmed_only"}:
        raise ValueError("請確認未定晉級選手的負荷計算方式。")
    config["player_workload"] = {
        "minimum_rest_minutes": numbers["rest"],
        "max_matches_per_day": numbers["daily_limit"],
        "participant_scope": scope,
    }
    best, target = settings.get("best_of"), settings.get("target")
    if best not in {3, 5} or target not in {11, 15}:
        raise ValueError("請選擇三／五局制及 11／15 分制。")
    config["match_rule_defaults"].update(
        best_of_games=best, points_to_win_game=target, win_by_points=2, point_cap_mode="none"
    )
    for key in RULE_FIELDS:
        value = settings.get(key, "")
        config["match_rule_defaults"][key] = rule_value(key, value)
    config["draw_defaults"].update(
        seeded_entries_mode="none", affiliation_avoidance_mode="disabled"
    )
    config["publication"].update(
        layout_template_id="reference-v2",
        paper_size="A4",
        orientation="landscape",
        qr_mode="disabled",
        sponsor_mode="none",
    )
    return EventConfig.model_validate(config).model_dump(mode="json")


class LocalWorkflow:
    def __init__(self, service: Any):
        self.service = service
        self.uploads: dict[str, dict] = {}
        self.plans: dict[str, dict] = {}

    def initialized(self) -> bool:
        return (self.service.repo.root / "working" / "current.json").is_file()

    def state(self) -> dict:
        with self.service.guard:
            snapshot = self.service.repo.load() if self.initialized() else None
            return {
                "initialized": snapshot is not None,
                "workspace": self.service.repo.root.name,
                "revision_id": snapshot.id if snapshot else None,
                "cloud_compatibility": snapshot_cloud_compatibility(snapshot) if snapshot else None,
                "rules": {
                    key: snapshot.config.get("match_rule_defaults", {}).get(key)
                    for key in RULE_FIELDS
                }
                if snapshot
                else {},
                "uploads": [
                    {k: v for k, v in item.items() if k != "raw"} for item in self.uploads.values()
                ],
            }

    def upload(self, filename: str, raw: bytes) -> dict:
        with self.service.guard:
            identifier = hashlib.sha256(raw).hexdigest()
            if identifier in self.uploads:
                return {k: v for k, v in self.uploads[identifier].items() if k != "raw"}
            if sum(len(v["raw"]) for v in self.uploads.values()) + len(raw) > 50 * 1024 * 1024:
                raise ValueError("待核對檔案合計超過 50 MB，請重啟操作台後分批處理。")
            parsed = parse_intake(filename, raw)
            identifier = parsed["sha256"]
            item = {**parsed, "id": identifier, "raw": raw}
            self.uploads[identifier] = item
            return {k: v for k, v in item.items() if k != "raw"}

    def preview(self, request: dict) -> dict:
        with self.service.guard:
            if self.initialized():
                raise ValueError(
                    "此工作區已有賽事。請另開新工作區建立賽事，既有賽事可繼續使用操作台。"
                )
            actor, reason = request.get("actor", "").strip(), request.get("reason", "").strip()
            if not actor or not reason:
                raise ValueError("請填寫操作者及建立原因。")
            rows, uploads = request.get("rows"), request.get("uploads")
            if not isinstance(rows, list) or not 2 <= len(rows) <= 2000:
                raise ValueError("請核對 2 至 2,000 筆報名資料。")
            if (
                not isinstance(uploads, list)
                or len(set(uploads)) != len(uploads)
                or any(i not in self.uploads for i in uploads)
            ):
                raise ValueError("來源檔案已失效，請重新上傳。")
            rows = deepcopy(rows)
            divisions = []
            for row in rows:
                if not isinstance(row, dict) or any(
                    not isinstance(row.get(k), str) or not row[k].strip() or len(row[k]) > 100
                    for k in ("name", "division", "identity")
                ):
                    raise ValueError("每筆報名須有姓名、組別及已核對的選手身分。")
                for key in ("name", "division", "identity"):
                    row[key] = row[key].strip()
                if row["division"] not in divisions:
                    divisions.append(row["division"])
            config = event_config(request.get("settings", {}), divisions, actor)
            counts = Counter(row["division"] for row in rows)
            classifications = request["settings"]["division_classifications"]
            small_groups = []
            for division in config["divisions"]:
                count = counts[division["name"]]
                if count >= 3:
                    continue
                action = classifications[division["name"]].get("small_count_action")
                required = "exhibition_only" if count == 1 else "direct_match"
                if action != required:
                    raise ValueError(f"請明確確認 {division['name']} 的 {count} 人組處置。")
                if count == 1:
                    division["status"] = "exhibition_only"
                small_groups.append(
                    {
                        "division": division["name"],
                        "entries": count,
                        "action": action,
                        "label": "保留名單，不編排競賽" if count == 1 else "兩人直接對賽",
                    }
                )
            snapshot = Snapshot(
                config=config, divisions=[Division.model_validate(d) for d in config["divisions"]]
            )
            division_ids = {d.name: d.id for d in snapshot.divisions}
            batch_id = new_id()
            candidates, resolutions = [], []
            identities: dict[str, str] = {}
            review_raw = json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
            review_digest = hashlib.sha256(review_raw).hexdigest()
            review_source = stable_id("intake-source", review_digest)
            for index, row in enumerate(rows):
                source_id = row.get("source_id")
                if source_id and source_id not in uploads:
                    raise ValueError("報名資料來源不在本次上傳清單。")
                ref = {"reviewed_row": index + 1, "original": deepcopy(row.get("source_ref", {}))}
                if source_id:
                    ref.update(
                        source_file_id=stable_id("intake-source", source_id), sha256=source_id
                    )
                else:
                    ref.update(manual_entry=True, source_file_id=review_source)
                cid = stable_id(batch_id, str(index))
                division_id = division_ids[row["division"]]
                candidates.append(
                    {
                        "id": cid,
                        "official_name": row["name"].strip(),
                        "division_id": division_id,
                        "source_ref": ref,
                        "status": "pending_review",
                        "ignored_reason": None,
                    }
                )
                resolution: dict[str, Any] = {"candidate_id": cid, "division_id": division_id}
                key = row["identity"]
                if key in identities:
                    resolution["participant_id"] = identities[key]
                else:
                    pid = stable_id("roster-participant", batch_id, cid)
                    identities[key] = pid
                    resolution["create_participant"] = {
                        "id": pid,
                        "official_name": row["name"].strip(),
                        "display_name": row["name"].strip(),
                    }
                resolutions.append(resolution)
            snapshot.imports.append(
                {
                    "id": batch_id,
                    "source_file_id": review_source,
                    "status": "staged",
                    "candidates": candidates,
                    "issues": [],
                    "parser_version": "local-intake-1",
                    "sha256": canonical_hash(rows),
                }
            )
            resolve_roster(
                snapshot,
                {
                    "batch_id": batch_id,
                    "actor_id": actor,
                    "reason": reason,
                    "resolutions": resolutions,
                },
            )
            from .domain.revisions import apply_patch, approve
            from .domain.validation import target_basis

            for division in snapshot.divisions:
                if counts[division.name] != 2:
                    continue
                snapshot, _ = apply_patch(
                    snapshot,
                    {
                        "base_revision_id": snapshot.id,
                        "actor_id": actor,
                        "reason": reason,
                        "operations": [
                            {
                                "op": "resolve_division_count",
                                "division_id": division.id,
                                "action": "direct_match",
                            }
                        ],
                    },
                )
                resolved = next(d for d in snapshot.divisions if d.id == division.id)
                snapshot, _ = approve(
                    snapshot,
                    "division_count:" + division.id,
                    target_basis(resolved.rule_overrides["count_resolution"]),
                    actor,
                    reason,
                )
            for identifier in uploads:
                item = self.uploads[identifier]
                snapshot.sources.append(
                    {
                        "id": stable_id("intake-source", identifier),
                        "sha256": identifier,
                        "original_name": item["filename"],
                        "media_type": item["kind"],
                        "size_bytes": len(item["raw"]),
                        "imported_at": utcnow().isoformat(),
                        "parser_version": "local-intake-1",
                    }
                )
            snapshot.sources.append(
                {
                    "id": review_source,
                    "sha256": review_digest,
                    "original_name": "reviewed-rows.json",
                    "media_type": "application/json",
                    "size_bytes": len(review_raw),
                    "imported_at": utcnow().isoformat(),
                    "parser_version": "local-intake-1",
                }
            )
            identifier = new_id()
            summary = {
                "title": "建立本地賽事",
                "event": config["event"],
                "entries": len(rows),
                "participants": len(identities),
                "divisions": divisions,
                "settings": request["settings"],
                "small_groups": small_groups,
                "cloud_compatibility": cloud_compatibility(request["settings"]),
                "identity_review": identity_review(rows),
                "notice": "姓名身分與設定由本次核對確認；各組賽制與抽籤仍須在操作台逐項確認。未填寫的簡章條文保留待補。",
            }
            if (
                request["settings"].get("participant_scope") != "confirmed_only"
                or request["settings"].get("rest") != 60
                or request["settings"].get("daily_limit") != 5
            ):
                summary["notice"] += (
                    " 本次負荷設定僅供地端使用，與現有雲端後台不相容；雲端匯出會停止。"
                )
            plan = {
                "id": identifier,
                "summary": summary,
                "actor": actor,
                "reason": reason,
                "snapshot": snapshot,
                "uploads": uploads,
                "review_raw": review_raw,
            }
            plan["hash"] = canonical_hash(
                {"id": identifier, "content": content_hash(snapshot), "summary": summary}
            )
            self.plans.clear()
            self.plans[identifier] = plan
            return {"id": identifier, "hash": plan["hash"], "summary": summary}

    def confirm(self, request: dict) -> dict:
        with self.service.guard, self.service.repo.lock():
            plan = self.plans.get(request.get("id", ""))
            if plan is None or request.get("hash") != plan["hash"]:
                raise ValueError("建立預覽已失效，請重新核對。")
            if self.initialized():
                raise ValueError("此工作區已有賽事，不能覆蓋建立。")
            snapshot = plan["snapshot"].model_copy(deep=True)
            for metadata in snapshot.sources:
                item = self.uploads.get(
                    metadata["sha256"],
                    {"filename": "reviewed-rows.json", "raw": plan["review_raw"]},
                )
                with tempfile.TemporaryDirectory(prefix="pk-intake-") as folder:
                    path = Path(folder) / item["filename"]
                    path.write_bytes(item["raw"])
                    self.service.repo.archive_source(path, metadata)
            snapshot.changes.append(
                {
                    "id": new_id(),
                    "kind": "local_setup",
                    "actor_id": plan["actor"],
                    "reason": plan["reason"],
                    "created_at": utcnow().isoformat(),
                    "preview_hash": plan["hash"],
                }
            )
            saved = self.service.repo.save(snapshot, None)
            self.plans.clear()
            self.uploads.clear()
            return {"revision_id": saved.id}

    def font(self, raw: bytes, request: dict) -> dict:
        from .exporters.assets import import_asset

        with self.service.guard, self.service.repo.lock():
            snapshot = self.service.repo.load()
            if request.get("revision_id") != snapshot.id:
                raise ValueError("賽事版本已更新，請重新整理後再匯入字型。")
            actor = request.get("actor", "").strip()
            if not actor:
                raise ValueError("請填寫操作者。")
            with tempfile.TemporaryDirectory(prefix="pk-font-") as folder:
                path = Path(folder) / "font.ttf"
                path.write_bytes(raw)
                asset = import_asset(self.service.repo.root, path, "font")
            snapshot.config["publication"]["font_asset_id"] = asset["asset_id"]
            snapshot.status = "draft"
            for approval in snapshot.approvals:
                if approval.get("target_type") in {"revision", "publication"}:
                    approval["status"] = "stale"
            snapshot.changes.append(
                {
                    "id": new_id(),
                    "kind": "font_import",
                    "actor_id": actor,
                    "reason": "由操作台匯入已確認可使用的本機字型",
                    "asset_id": asset["asset_id"],
                    "created_at": utcnow().isoformat(),
                }
            )
            stored = self.service.repo.save(snapshot, snapshot.id)
            return {"revision_id": stored.id, "asset": asset}


def execute_event_rules(
    snapshot: Snapshot, rules: dict, actor: str, reason: str
) -> tuple[Snapshot, dict]:
    from .domain.revisions import apply_patch

    if not isinstance(rules, dict) or not rules or not set(rules) <= set(RULE_FIELDS):
        raise ValueError("請只修改簡章條文欄位。")
    current = deepcopy(snapshot.config.get("match_rule_defaults", {}))
    changes = []
    labels = dict(
        zip(
            RULE_FIELDS,
            (
                "遲到處理",
                "棄權處理",
                "傷退處理",
                "雙方棄權",
                "替補規定",
                "排名計算分母",
                "多人同分處理",
            ),
            strict=True,
        )
    )
    for key, value in rules.items():
        value = rule_value(key, value)
        if current.get(key) != value:
            changes.append({"label": labels[key], "before": current.get(key), "after": value})
            current[key] = value
    if not changes:
        raise ValueError("簡章條文沒有變更。")
    working, _ = apply_patch(
        snapshot,
        {
            "base_revision_id": snapshot.id,
            "actor_id": actor,
            "reason": reason,
            "operations": [{"op": "set_config", "field": "match_rule_defaults", "value": current}],
        },
    )
    return working, {
        "title": "修改簡章條文",
        "source_revision": snapshot.id,
        "changes": changes,
        "notice": "確認後建立新版本。已開賽場次保留原規則；未開賽場次依既有規則更新。PDF、Excel 與離線網站需重新產生。",
    }
