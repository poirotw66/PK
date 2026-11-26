"""Allowlisted spectator snapshots, independent of operator state and HTTP transport.

The same JSON contract can be served locally or published to object storage.
No source records, operator credentials, audit reasons or filesystem paths belong here.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .domain.match_rules import effective_match_rules
from .domain.models import Snapshot, canonical_hash
from .domain.rankings import compute_rankings, resolve_side
from .domain.validation import validate
from .operator_actions import state_fields
from .storage.repository import Repository, RepositoryError


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicSide(PublicModel):
    name: str
    entry_id: str | None = None
    source_type: str
    source_match_id: str | None = None
    source_label: str | None = None
    resolution: Literal["confirmed", "pending", "bye"]


class PublicGame(PublicModel):
    side_a: int = Field(ge=0)
    side_b: int = Field(ge=0)


class PublicResult(PublicModel):
    outcome: Literal["played", "walkover", "retired", "double_forfeit", "cancelled"]
    winner_entry_id: str | None
    games: list[PublicGame]


class PublicMatch(PublicModel):
    id: str
    code: str
    division_id: str
    stage_id: str
    round: int
    position: int
    kind: str
    status: str
    side_a: PublicSide
    side_b: PublicSide
    result: PublicResult | None
    court_id: str | None
    planned_start_at: str | None
    called_at: str | None
    started_at: str | None
    delay_minutes: int
    delay_active: bool


class PublicDivision(PublicModel):
    id: str
    name: str
    entry_count: int | None = Field(default=None, ge=0)
    single_entry_name: str | None = None


class PublicStage(PublicModel):
    id: str
    division_id: str
    name: str
    type: str
    order: int
    bye_names: list[str] = Field(default_factory=list)


class PublicCourt(PublicModel):
    id: str
    name: str


class PublicStanding(PublicModel):
    entry_id: str
    name: str
    status: str
    rank: int | None
    wins: int
    losses: int
    forfeits: int
    played_count: int
    adjudicated_count: int
    points: float
    games_for: int
    games_against: int
    qualifies: bool


class PublicStandingsGroup(PublicModel):
    stage_id: str
    division_id: str
    pool_id: str | None
    name: str
    status: Literal["provisional", "manual_review", "final"]
    notice: str
    scoring_label: str
    advancement_count: int | None
    confirmed_matches: int
    total_matches: int
    tie_groups: list[list[str]]
    rows: list[PublicStanding]


class PublicSnapshot(PublicModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    version: str
    workspace_id: str
    revision_id: str
    updated_at: str
    name: str
    timezone: str
    publication_status: Literal["ready", "pending_review"]
    notice: str
    divisions: list[PublicDivision]
    stages: list[PublicStage]
    courts: list[PublicCourt]
    matches: list[PublicMatch]
    standings: list[PublicStandingsGroup] = Field(default_factory=list)


def project_snapshot(snapshot: Snapshot, workspace_id: str) -> PublicSnapshot:
    """Construct public fields explicitly; never serialize an internal object wholesale."""
    event = snapshot.config.get("event", {})
    ready = validate(snapshot, "G3")["passed"]
    visible = snapshot.matches if ready else []
    codes = {m.id: m.display_code or f"M{i:03}" for i, m in enumerate(snapshot.matches, 1)}
    people = {p.id: p.display_name for p in snapshot.participants}
    registrations = {r.id: r.participant_id for r in snapshot.registrations}
    names = {e.id: people[registrations[e.registration_id]] for e in snapshot.entries}
    pools = {p.id: p.label for p in snapshot.pools}
    entries = {e.id: e for e in snapshot.entries}
    results = {r["match_id"]: r for r in snapshot.results if r.get("status") == "confirmed"}
    assignments = {a.match_id: a for a in snapshot.assignments}
    live = (
        {
            row["match_id"]: row
            for court in state_fields(snapshot)["live_board"]["courts"]
            for row in court["matches"]
        }
        if ready
        else {}
    )

    def side(value) -> PublicSide:
        entry_id = resolve_side(snapshot, value)
        label, source = None, None
        if value.type in {"match_winner", "match_loser"}:
            source = value.match_id
            label = f"{codes.get(source, '待確認')} 場{'勝者' if value.type == 'match_winner' else '敗者'}"
        elif value.type == "pool_rank":
            label = f"{pools.get(value.pool_id, '分組')} 第 {value.rank} 名" + (
                "（待核定）" if entry_id is None else ""
            )
        elif value.type == "draw_slot":
            label = "抽位待確認" if entry_id is None else "已核准現場抽位"
        elif value.type == "bye":
            label = "輪空"
        return PublicSide(
            name=names.get(entry_id or "", label or "對手待確認"),
            entry_id=entry_id,
            source_type=value.type,
            source_match_id=source,
            source_label=label,
            resolution="confirmed" if entry_id else "bye" if value.type == "bye" else "pending",
        )

    rows = []
    for match in visible:
        assignment, facts = assignments.get(match.id), live.get(match.id, {})
        a, b = side(match.side_a), side(match.side_b)
        result = results.get(match.id)
        public_result = None
        if result and result.get("outcome") in {
            "played",
            "walkover",
            "retired",
            "double_forfeit",
            "cancelled",
        }:
            public_result = PublicResult(
                outcome=result["outcome"],
                winner_entry_id=result.get("winner_id"),
                games=[
                    PublicGame(side_a=g["side_a"], side_b=g["side_b"])
                    for g in result.get("games", [])
                ],
            )
        rows.append(
            PublicMatch(
                id=match.id,
                code=codes[match.id],
                division_id=match.division_id,
                stage_id=match.stage_id,
                round=match.round,
                position=match.bracket_position,
                kind=match.kind,
                status=match.status,
                side_a=a,
                side_b=b,
                result=public_result,
                court_id=facts.get("actual_court_id")
                or (assignment.court_id if assignment else None),
                planned_start_at=assignment.start_at.isoformat() if assignment else None,
                called_at=facts.get("called_at"),
                started_at=facts.get("started_at"),
                delay_minutes=facts.get("delay_minutes", 0),
                delay_active=facts.get("delay_active", False),
            )
        )
    stage_names = {"round_robin": "循環賽", "elimination": "淘汰賽", "placement": "名次賽"}
    bye_names = {}
    if ready:
        for stage in snapshot.stages:
            if stage.stage_key != "encouragement":
                continue
            record = next(
                d
                for d in snapshot.draws
                if d.get("kind") == "encouragement"
                and d.get("division_id") == stage.division_id
                and d.get("status") == "approved"
            )
            bracket = record["output"]["bracket_entry_ids"]
            bye_names[stage.id] = [
                names[bracket[i ^ 1]]
                for i, entry in enumerate(bracket)
                if entry is None and bracket[i ^ 1] in names
            ]
    standings = []
    for stage in snapshot.stages if ready else []:
        if stage.type != "round_robin":
            continue
        report = compute_rankings(snapshot, stage.id)
        for group in report["groups"]:
            pool = next((p for p in snapshot.pools if p.id == group["pool_id"]), None)
            member_ids = {row["entry_id"] for row in group["standings"]}
            matches = [
                m
                for m in visible
                if m.stage_id == stage.id
                and m.kind != "exhibition"
                and getattr(m.side_a, "entry_id", None) in member_ids
                and getattr(m.side_b, "entry_id", None) in member_ids
            ]
            rules = [
                results[m.id].get("rule_snapshot") or effective_match_rules(snapshot, m)
                if m.id in results
                else effective_match_rules(snapshot, m)
                for m in matches
            ]
            scores = {(r["scoring"]["win_points"], r["scoring"]["loss_points"]) for r in rules}
            if len(scores) == 1:
                win, loss = next(iter(scores))
                scoring_label = f"勝:{win:g}分 敗:{loss:g}分"
                forfeits = [r.get("walkover_rule") for r in rules]
                if all(
                    isinstance(rule, dict)
                    and rule.get("include_in_rankings")
                    and rule.get("winner_points") == win
                    for rule in forfeits
                ):
                    points = {rule["loser_points"] for rule in forfeits if isinstance(rule, dict)}
                    scoring_label += (
                        f" 棄:{next(iter(points)):g}分"
                        if len(points) == 1
                        else "；棄權依各場核定規則"
                    )
                else:
                    scoring_label += "；棄權／退賽依各場核定規則"
            else:
                scoring_label = "積分依各場已確認規則計算"
            status = group["status"]
            notice = {
                "provisional": "積分暫列，名次待全組結果確認。",
                "final": "排名已確認。",
                "manual_review": "排名待核定，不自動決定晉級。",
            }[status]
            if status == "manual_review" and group["tie_groups"] and not group["incomplete"]:
                notice = "同分待裁定，不自動決定晉級。"
            standings.append(
                PublicStandingsGroup(
                    stage_id=stage.id,
                    division_id=stage.division_id,
                    pool_id=group["pool_id"],
                    name=pool.label if pool else stage.label or "全組積分",
                    status=status,
                    notice=notice,
                    scoring_label=scoring_label,
                    advancement_count=pool.advancement_count if pool else None,
                    confirmed_matches=sum(
                        m.id in results
                        and results[m.id].get("outcome") not in {"pending", "cancelled"}
                        for m in matches
                        if m.status not in {"excluded", "cancelled"}
                    ),
                    total_matches=sum(m.status not in {"excluded", "cancelled"} for m in matches),
                    tie_groups=group["tie_groups"],
                    rows=[
                        PublicStanding(
                            entry_id=row["entry_id"],
                            name=names[row["entry_id"]],
                            status=entries[row["entry_id"]].status,
                            rank=row["rank"] if status == "final" else None,
                            wins=row["wins"],
                            losses=row["losses"],
                            forfeits=row["forfeits"],
                            played_count=row["played_count"],
                            adjudicated_count=row["adjudicated_count"],
                            points=row["points"],
                            games_for=row["games_for"],
                            games_against=row["games_against"],
                            qualifies=status == "final"
                            and entries[row["entry_id"]].status == "active"
                            and pool is not None
                            and row["rank"] <= pool.advancement_count,
                        )
                        for row in group["standings"]
                    ],
                )
            )
    public = PublicSnapshot(
        version="",
        workspace_id=workspace_id,
        revision_id=snapshot.id,
        updated_at=snapshot.created_at.isoformat(),
        name=event.get("name") or "壁球賽事",
        timezone=event.get("timezone") or "Asia/Taipei",
        publication_status="ready" if ready else "pending_review",
        notice="賽況以賽務確認為準。" if ready else "籤表或賽事資料更新中，待賽務確認後顯示。",
        divisions=[
            PublicDivision(
                id=d.id,
                name=d.name,
                entry_count=sum(
                    e.effective_division_id == d.id and e.status == "active"
                    for e in snapshot.entries
                ),
                single_entry_name=next(
                    (
                        names[e.id]
                        for e in snapshot.entries
                        if e.effective_division_id == d.id and e.status == "active"
                    ),
                    None,
                )
                if sum(
                    e.effective_division_id == d.id and e.status == "active"
                    for e in snapshot.entries
                )
                == 1
                and not any(m.division_id == d.id for m in visible)
                else None,
            )
            for d in snapshot.divisions
        ],
        stages=[
            PublicStage(
                id=s.id,
                division_id=s.division_id,
                name=s.label or stage_names[s.type],
                type=s.type,
                order=s.order,
                bye_names=bye_names.get(s.id, []),
            )
            for s in snapshot.stages
        ]
        if ready
        else [],
        courts=[
            PublicCourt(id=c["id"], name=c["label"]) for c in snapshot.config.get("courts", [])
        ],
        matches=rows,
        standings=standings,
    )
    public.version = canonical_hash(public.model_dump(mode="json", exclude={"version"}))
    return public


class PublicFeed:
    """Cache a verified projection by workspace and atomic current-file identity.

    The owner holds its workspace guard. Polls with no file change only stat the
    current pointer; they do not reload history, run validation or resolve rankings.
    """

    def __init__(self):
        self._key: tuple | None = None
        self._payload: dict = {}

    def _current(self, repository: Repository) -> dict:
        path = repository.root / "working" / "current.json"

        def identity() -> tuple:
            stat = path.stat()
            return (
                str(repository.root),
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )

        key = identity()
        if key != self._key:
            for _ in range(3):
                before = identity()
                snapshot = repository.load()
                if identity() == before:
                    workspace_id = canonical_hash({"workspace": str(repository.root)})[:24]
                    self._payload = project_snapshot(snapshot, workspace_id).model_dump(mode="json")
                    self._key = before
                    break
            else:
                raise RepositoryError("Public snapshot changed while reading")
        return self._payload

    def read(self, repository: Repository) -> dict:
        return deepcopy(self._current(repository))

    def version(self, repository: Repository) -> dict:
        payload = self._current(repository)
        return {
            **{
                k: payload[k]
                for k in ("schema_version", "version", "workspace_id", "revision_id", "updated_at")
            },
            "snapshot_url": "snapshot.json?version=" + payload["version"],
        }
