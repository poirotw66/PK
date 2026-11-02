"""Versioned contract. Unknown event parameters stay unknown."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)


def new_id() -> str:
    return str(uuid4())


def stable_id(namespace: str, *parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, json.dumps([namespace, *parts], ensure_ascii=False)))


def canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid(value: str) -> str:
    return str(UUID(value))


ID = Annotated[str, AfterValidator(_uuid), Field(json_schema_extra={"format": "uuid"})]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @field_validator("*", mode="after")
    @classmethod
    def normalize_text(cls, value: Any) -> Any:
        return unicodedata.normalize("NFC", value) if isinstance(value, str) else value


class Issue(Model):
    code: str
    severity: Literal["error", "warning", "info"] = "error"
    gate: Literal["G1", "G2", "G3", "G4", "G5", "G6"] = "G1"
    entity_type: str = ""
    entity_id: str | None = None
    field_path: str = ""
    message: str
    suggested_action: str = ""
    status: Literal["open", "resolved"] = "open"
    resolution_id: str | None = None


class Participant(Model):
    id: ID = Field(default_factory=new_id)
    official_name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    identity_status: Literal["pending_review", "confirmed"] = "confirmed"
    affiliation_ids: list[ID] = Field(default_factory=list)
    minimum_rest_minutes: int | None = Field(default=None, ge=0)


class Registration(Model):
    id: ID = Field(default_factory=new_id)
    participant_id: ID
    original_division_id: ID
    source_ref: dict[str, Any]
    status: Literal[
        "pending_review", "accepted", "withdrawn", "rejected", "historical_unresolved"
    ] = "accepted"


class Entry(Model):
    id: ID = Field(default_factory=new_id)
    registration_id: ID
    effective_division_id: ID
    status: Literal["active", "transferred", "withdrawn", "cancelled"] = "active"


class Division(Model):
    id: ID = Field(default_factory=new_id)
    name: str
    category: Literal["competitive", "school"] | None = None
    gender_category: str | None = None
    level_order: int | None = None
    minimum_entries: int = Field(default=3, ge=1)
    status: Literal["draft", "active", "cancelled", "merged", "exhibition_only"] = "draft"
    format_plan: dict[str, Any] | None = None
    rule_overrides: dict[str, Any] = Field(default_factory=dict)
    draw_overrides: dict[str, Any] = Field(default_factory=dict)


class Stage(Model):
    id: ID = Field(default_factory=new_id)
    division_id: ID
    type: Literal["round_robin", "elimination", "placement"]
    order: int = Field(default=0, ge=0)
    label: str | None = None
    stage_key: str | None = None
    rule_set_id: str | None = None

    @model_serializer(mode="wrap")
    def serialize_stage(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        for field in ("label", "stage_key"):
            if data.get(field) is None:
                data.pop(field, None)
        return data


class Pool(Model):
    id: ID = Field(default_factory=new_id)
    stage_id: ID
    label: str
    entry_ids: list[ID]
    advancement_count: int = Field(gt=0)

    @model_validator(mode="after")
    def valid_members(self) -> Pool:
        if len(self.entry_ids) != len(set(self.entry_ids)):
            raise ValueError("Pool entries must be unique")
        if self.advancement_count > len(self.entry_ids):
            raise ValueError("Advancement count exceeds pool size")
        return self


class EntrySide(Model):
    type: Literal["entry"] = "entry"
    entry_id: ID


class PoolRankSide(Model):
    type: Literal["pool_rank"] = "pool_rank"
    pool_id: ID
    rank: int = Field(gt=0)


class WinnerSide(Model):
    type: Literal["match_winner"] = "match_winner"
    match_id: ID


class LoserSide(Model):
    type: Literal["match_loser"] = "match_loser"
    match_id: ID


class DrawSlotSide(Model):
    type: Literal["draw_slot"] = "draw_slot"
    draw_run_id: ID
    slot_id: str


class ByeSide(Model):
    type: Literal["bye"] = "bye"


MatchSide = Annotated[
    EntrySide | PoolRankSide | WinnerSide | LoserSide | DrawSlotSide | ByeSide,
    Field(discriminator="type"),
]
MatchWinnerSide = WinnerSide
MatchLoserSide = LoserSide


class Match(Model):
    id: ID = Field(default_factory=new_id)
    division_id: ID
    stage_id: ID
    kind: Literal["competitive", "exhibition", "placement"] = "competitive"
    side_a: MatchSide
    side_b: MatchSide
    status: Literal["planned", "ready", "in_progress", "completed", "cancelled", "excluded"] = (
        "planned"
    )
    round: int = Field(default=1, ge=1)
    bracket_position: int = Field(default=0, ge=0)
    display_code: str | None = None
    rule_snapshot: dict[str, Any] | None = None


class Assignment(Model):
    match_id: ID
    court_id: ID
    start_at: datetime
    play_end_at: datetime
    court_release_at: datetime
    locks: dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_times(self) -> Assignment:
        for dt in (self.start_at, self.play_end_at, self.court_release_at):
            if dt.utcoffset() is None or dt.second or dt.microsecond:
                raise ValueError("Assignment timestamps require timezone and whole minutes")
        if not self.start_at < self.play_end_at <= self.court_release_at:
            raise ValueError("Assignment end times are inconsistent")
        return self


class Snapshot(Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    id: ID = Field(default_factory=new_id)
    parent_id: ID | None = None
    status: Literal["draft", "review_required", "approved", "superseded"] = "draft"
    created_at: datetime = Field(default_factory=utcnow)
    config: dict[str, Any]
    participants: list[Participant] = Field(default_factory=list)
    registrations: list[Registration] = Field(default_factory=list)
    entries: list[Entry] = Field(default_factory=list)
    divisions: list[Division] = Field(default_factory=list)
    stages: list[Stage] = Field(default_factory=list)
    pools: list[Pool] = Field(default_factory=list)
    matches: list[Match] = Field(default_factory=list)
    assignments: list[Assignment] = Field(default_factory=list)
    draws: list[dict[str, Any]] = Field(default_factory=list)
    exclusions: list[dict[str, Any]] = Field(default_factory=list)
    results: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    changes: list[dict[str, Any]] = Field(default_factory=list)
    imports: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    ranking_decisions: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("created_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Audit timestamps require timezone")
        return value

    @model_validator(mode="after")
    def unique_keys(self) -> Snapshot:
        for name in (
            "participants",
            "registrations",
            "entries",
            "divisions",
            "stages",
            "pools",
            "matches",
        ):
            values = getattr(self, name)
            ids = [v.id for v in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate IDs in {name}")
        return self


def content_payload(snapshot: Snapshot) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    for key in ("id", "parent_id", "status", "created_at", "approvals", "issues", "changes"):
        payload.pop(key, None)
    return payload


def content_hash(snapshot: Snapshot) -> str:
    return canonical_hash(content_payload(snapshot))
