"""Manual schedule drafts and human-readable before/after review, without storage writes."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .domain.models import Assignment, Snapshot, canonical_hash
from .domain.rankings import resolve_side
from .domain.validation import validate
from .exporters.messages import issue_label
from .scheduling.constraints import effective_timing, validate_schedule

UUID_PATTERN = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)


def manual_draft(snapshot: Snapshot, updates: list) -> Snapshot:
    if not isinstance(updates, list) or not 1 <= len(updates) <= 500:
        raise ValueError("請提供 1 至 500 筆場次調整。")
    working = snapshot.model_copy(deep=True)
    matches = {m.id: m for m in working.matches}
    assignments = {a.match_id: a for a in working.assignments}
    zone = ZoneInfo(working.config["event"]["timezone"])
    seen = set()
    for update in updates:
        if not isinstance(update, dict) or set(update) != {"match_id", "local_start", "court_id"}:
            raise ValueError("場次調整須包含場次、賽事當地時間與場地。")
        mid = update["match_id"]
        if not isinstance(mid, str) or mid not in matches or mid in seen:
            raise ValueError("場次不存在或重複加入草案。")
        seen.add(mid)
        value = update["local_start"]
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", value):
            raise ValueError("時間須為賽事時區的 YYYY-MM-DDTHH:MM 格式。")
        local = datetime.fromisoformat(value)
        start = local.replace(tzinfo=zone)
        if (
            start.astimezone(ZoneInfo("UTC")).astimezone(zone).replace(tzinfo=None) != local
            or start.replace(fold=0).utcoffset() != start.replace(fold=1).utcoffset()
        ):
            raise ValueError("此當地時間不存在或有夏令時間歧義，請另選明確時間。")
        timing = effective_timing(working, matches[mid])
        duration, buffer = timing.get("estimated_play_minutes"), timing.get("court_buffer_minutes")
        if type(duration) is not int or duration <= 0 or type(buffer) is not int or buffer < 0:
            raise ValueError("場次的比賽時間或場地緩衝尚未設定。")
        prior = assignments.get(mid)
        assignments[mid] = Assignment(
            match_id=mid,
            court_id=update["court_id"],
            start_at=start,
            play_end_at=start + timedelta(minutes=duration),
            court_release_at=start + timedelta(minutes=duration + buffer),
            locks=prior.locks.copy() if prior else {},
        )
    working.assignments = list(assignments.values())
    return working


def schedule_review(before: Snapshot, after: Snapshot) -> dict:
    from .operator_actions import validate_live_schedule

    zone = ZoneInfo(before.config["event"]["timezone"])
    matches = {m.id: m for m in after.matches}
    courts = {c["id"]: c["label"] for c in after.config.get("courts", [])}
    divisions = {d.id: d.name for d in after.divisions}
    registrations = {r.id: r.participant_id for r in after.registrations}
    people = {p.id: p.display_name for p in after.participants}
    entry_names = {e.id: people[registrations[e.registration_id]] for e in after.entries}
    old, new = ({a.match_id: a for a in s.assignments} for s in (before, after))

    def side_name(side):
        entry = resolve_side(after, side)
        if entry:
            return entry_names.get(entry, "對手待確認")
        if side.type in {"match_winner", "match_loser"}:
            source = matches[side.match_id]
            return f"{source.display_code or source.id[:8]} 場{'勝者' if side.type == 'match_winner' else '敗者'}"
        return "對手待確認"

    def point(a):
        if a is None:
            return None
        local = a.start_at.astimezone(zone)
        return {
            "date": local.date().isoformat(),
            "time": local.strftime("%H:%M"),
            "start_at": local.isoformat(),
            "play_end_at": a.play_end_at.astimezone(zone).isoformat(),
            "court_id": a.court_id,
            "court": courts.get(a.court_id, "未知場地"),
        }

    changes = []
    for mid in sorted(
        old.keys() | new.keys(), key=lambda mid: ((new.get(mid) or old[mid]).start_at, mid)
    ):
        prior, proposed = point(old.get(mid)), point(new.get(mid))
        if prior == proposed:
            continue
        match = matches.get(mid)
        changed = [
            key
            for key in ("date", "time", "court")
            if prior is None
            or proposed is None
            or prior["court_id" if key == "court" else key]
            != proposed["court_id" if key == "court" else key]
        ]
        if (
            prior
            and proposed
            and prior["play_end_at"] != proposed["play_end_at"]
            and not any(key in changed for key in ("date", "time"))
        ):
            changed.append("duration")
        changes.append(
            {
                "match_id": mid,
                "code": match.display_code or mid[:8] if match else mid[:8],
                "division": divisions.get(match.division_id, "未指定組別")
                if match
                else "已移除場次",
                "players": f"{side_name(match.side_a)} 對 {side_name(match.side_b)}"
                if match
                else "已移除場次",
                "before": prior,
                "after": proposed,
                "changed_fields": changed,
            }
        )

    def collect(snapshot, baseline=None):
        issues = [
            *validate(snapshot, "G3")["issues"],
            *(
                i.model_dump(mode="json")
                for i in validate_schedule(snapshot, baseline_assignments=baseline)
            ),
        ]
        issues.extend(
            {
                "code": "LIVE_SCHEDULE_CONFLICT",
                "message": message,
                "severity": "error",
                "status": "open",
                "entity_id": None,
                "field_path": "",
            }
            for message in validate_live_schedule(snapshot)
        )
        result = {}
        assignments = {a.match_id: a for a in snapshot.assignments}
        for issue in issues:
            if issue.get("severity") != "error" or issue.get("status", "open") != "open":
                continue
            code, message = issue["code"], issue["message"]
            ids = set(UUID_PATTERN.findall(message)) | {issue.get("entity_id")}
            related = sorted(ids & matches.keys())
            affected = sorted(ids & people.keys())
            if str(issue.get("field_path", "")).startswith("participants."):
                affected = sorted(set(affected) | {issue["field_path"].split(".", 1)[1]})
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", message)
            identity = (
                {"code": code, "people": affected, "dates": dates}
                if code == "PLAYER_DAILY_LIMIT_EXCEEDED"
                else {"code": code, "matches": related, "field": issue.get("field_path", "")}
            )
            if not related and not affected:
                identity["message"] = message
            key = canonical_hash(identity)
            friendly = (
                issue_label(code)
                if code != "LIVE_SCHEDULE_CONFLICT"
                else "與現場實際開賽或延誤狀態衝突。"
            )
            detail = message
            labels = {
                **people,
                **courts,
                **{mid: f"第 {m.display_code or mid[:8]} 場" for mid, m in matches.items()},
            }
            for identifier in UUID_PATTERN.findall(message):
                detail = detail.replace(identifier, labels.get(identifier, "未確認資料"))
            if (
                code == "PLAYER_REST_VIOLATION"
                and len(related) == 2
                and all(mid in assignments for mid in related)
            ):
                first, second = sorted(
                    (assignments[mid] for mid in related), key=lambda a: a.start_at
                )
                gap = (second.start_at - first.play_end_at).total_seconds() / 60
                first_rules = effective_timing(snapshot, matches[first.match_id])
                second_rules = effective_timing(snapshot, matches[second.match_id])
                person_rest = max(
                    (
                        p.minimum_rest_minutes or 0
                        for p in snapshot.participants
                        if p.id in affected
                    ),
                    default=0,
                )
                required = max(
                    (snapshot.config.get("player_workload") or {}).get("minimum_rest_minutes") or 0,
                    first_rules["minimum_player_rest_minutes"],
                    second_rules["minimum_player_rest_minutes"],
                    person_rest,
                )
                if first.court_id != second.court_id:
                    required += max(
                        first_rules["court_transfer_minutes"],
                        second_rules["court_transfer_minutes"],
                    )
                detail = f"{'、'.join(people[p] for p in affected)}：{labels[first.match_id]} → {labels[second.match_id]}，預定休息 {gap:g} 分鐘，至少需要 {required} 分鐘。"
            elif code == "COURT_OVERLAP" and related:
                a = assignments.get(related[0])
                detail = f"{courts.get(a.court_id, '場地') if a else '場地'}：{'、'.join(labels[mid] for mid in related)}的比賽或整理緩衝重疊。"
            elif code == "PLAYER_DAILY_LIMIT_EXCEEDED" and (
                found := re.search(r"on (\d{4}-\d{2}-\d{2}): (\d+) matches exceed (\d+)", message)
            ):
                day, count, cap = found.groups()
                detail = f"{'、'.join(people.get(p, '未確認選手') for p in affected)}在 {day} 有相容路徑排定 {count} 場，上限為 {cap} 場。"
            result[key] = {
                "key": key,
                "code": code,
                "label": friendly,
                "detail": detail,
                "match_ids": related,
                "people": [people.get(p, "未確認選手") for p in affected],
            }
        return result

    prior_errors = collect(before)
    next_errors = collect(after, before.assignments)
    fixed = [prior_errors[k] for k in sorted(prior_errors.keys() - next_errors.keys())]
    introduced = [next_errors[k] for k in sorted(next_errors.keys() - prior_errors.keys())]
    remaining = [
        {**next_errors[k], "before_detail": prior_errors[k]["detail"]}
        for k in sorted(next_errors.keys() & prior_errors.keys())
    ]
    raw_policy = after.config.get("player_workload") or {}
    rest_floor = raw_policy.get("minimum_rest_minutes") or 0
    rests = [
        max(rest_floor, effective_timing(after, m).get("minimum_player_rest_minutes") or 0)
        for m in after.matches
        if m.status not in {"cancelled", "excluded"}
    ]
    return {
        "event_name": before.config.get("event", {}).get("name") or "壁球賽事",
        "timezone": str(zone),
        "base_revision_id": before.id,
        "changed_count": len(changes),
        "assignment_count": len(new),
        "changes": changes,
        "conflicts": {
            "before_count": len(prior_errors),
            "after_count": len(next_errors),
            "resolved": fixed,
            "introduced": introduced,
            "remaining": remaining,
        },
        "can_apply": not next_errors,
        "event_dates": before.config["event"]["event_dates"],
        "policy": {
            "minimum_rest_minutes": min(rests, default=rest_floor),
            "max_matches_per_day": raw_policy.get("max_matches_per_day"),
        },
    }
