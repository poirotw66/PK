"""Draft-compatible configuration contract and immutable policy verification."""

from __future__ import annotations

import json
from datetime import date, datetime, time
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from .models import ID, Division, Model


class Event(Model):
    id: ID | None = None
    name: str | None = None
    timezone: str | None = None
    event_dates: list[date] | None = None
    venue_name: str | None = None
    actor_id: str | None = None

    @model_validator(mode="after")
    def valid_event(self) -> Event:
        if self.timezone:
            ZoneInfo(self.timezone)
        if self.event_dates and len(self.event_dates) != len(set(self.event_dates)):
            raise ValueError("Event dates must be unique")
        return self


class TimeRange(Model):
    start_at: datetime
    end_at: datetime

    @model_validator(mode="after")
    def valid_range(self) -> TimeRange:
        if any(
            t.utcoffset() is None or t.second or t.microsecond for t in (self.start_at, self.end_at)
        ):
            raise ValueError("Court ranges require timezone and whole minutes")
        if self.start_at >= self.end_at:
            raise ValueError("Court range must have positive duration")
        return self


class Court(Model):
    id: ID
    label: str
    sort_order: int = Field(ge=0)
    availability: list[TimeRange]
    blackouts: list[TimeRange] = Field(default_factory=list)


class GridAnchor(Model):
    date: date
    local_time: time


class Timing(Model):
    start_grid_minutes: int | None = Field(default=None, gt=0, strict=True)
    daily_grid_anchors: list[GridAnchor] | None = None
    estimated_play_minutes: int | None = Field(default=None, gt=0, strict=True)
    court_buffer_minutes: int | None = Field(default=None, ge=0, strict=True)
    minimum_player_rest_minutes: int | None = Field(default=None, ge=0, strict=True)
    court_transfer_minutes: int | None = Field(default=None, ge=0, strict=True)
    ranking_confirmation_minutes: int | None = Field(default=None, ge=0, strict=True)


class PlayerWorkload(Model):
    """Event-wide floors/caps across every registration of the same participant."""

    minimum_rest_minutes: int | None = Field(default=None, ge=0, strict=True)
    max_matches_per_day: int | None = Field(default=None, gt=0, strict=True)
    participant_scope: Literal["all_possible", "confirmed_only"] = "all_possible"


class MatchRules(Model):
    best_of_games: int | None = Field(default=None, gt=0, strict=True)
    points_to_win_game: int | None = Field(default=None, gt=0, strict=True)
    win_by_points: int | None = Field(default=None, gt=0, strict=True)
    point_cap_mode: Literal["unconfigured", "none", "enabled"] = "unconfigured"
    point_cap: int | None = Field(default=None, gt=0, strict=True)
    late_arrival_rule: dict[str, Any] | str | None = None
    walkover_rule: dict[str, Any] | str | None = None
    retirement_rule: dict[str, Any] | str | None = None
    double_forfeit_rule: dict[str, Any] | str | None = None
    replacement_rule: dict[str, Any] | str | None = None
    ranking_denominator_rule: dict[str, Any] | str | None = None
    multiway_tie_rule: dict[str, Any] | str | None = None

    @model_validator(mode="after")
    def valid_scoring(self) -> MatchRules:
        if self.best_of_games is not None and self.best_of_games % 2 == 0:
            raise ValueError("Best-of games must be odd")
        if self.point_cap_mode == "enabled" and (
            self.point_cap is None
            or self.points_to_win_game is None
            or self.point_cap < self.points_to_win_game
        ):
            raise ValueError("Enabled cap requires cap at or above target points")
        return self


class DrawConfig(Model):
    seeded_entries_mode: Literal["unconfigured", "none", "provided"] = "unconfigured"
    seeded_entries: list[dict[str, Any]] = Field(default_factory=list)
    affiliation_avoidance_mode: Literal["unconfigured", "disabled", "hard", "soft"] = "unconfigured"
    affiliation_avoidance_scope: str | None = None
    bye_allocation_rule: dict[str, Any] | str | None = None
    special_slot_rules: dict[str, Any] | list[Any] | str | None = None


class Prospectus(Model):
    """Organizer supplied public terms; omitted details must remain unknown."""

    approval_status: Literal["draft", "approved"] = "draft"
    organizers: str | list[str] | None = None
    co_organizers: str | list[str] | None = None
    eligibility: str | list[str] | None = None
    registration: str | list[str] | None = None
    check_in: str | list[str] | None = None
    competition: str | list[str] | None = None
    awards: str | list[str] | None = None
    appeals: str | list[str] | None = None
    contact: str | list[str] | None = None


class Publication(Model):
    document_profile: Literal["order_book", "prospectus"] = "order_book"
    prospectus: Prospectus | None = None
    draft_note: str | None = None
    layout_template_id: str | None = None
    historical_layout_verified: bool = False
    paper_size: Literal["A4", "A3"] | None = None
    orientation: Literal["portrait", "landscape"] | None = None
    font_asset_id: str | None = None
    qr_mode: Literal["unconfigured", "enabled", "disabled"] = "unconfigured"
    qr_url: str | None = None
    qr_label: str | None = None
    sponsor_mode: Literal["unconfigured", "provided", "none"] = "unconfigured"
    sponsors: list[dict[str, Any]] = Field(default_factory=list)


class PreferredScheduleWindow(TimeRange):
    division_id: ID
    stage_type: Literal["round_robin", "elimination", "placement"]
    rounds: list[Annotated[int, Field(gt=0, strict=True)]] | None = None


class Runtime(Model):
    coach_competition_rules: bool = Field(default=False, strict=True)
    keep_round_robin_groups_same_day: bool = Field(default=False, strict=True)
    keep_elimination_rounds_in_order: bool = Field(default=False, strict=True)
    batch_slot_minutes: int = Field(default=15, gt=0, le=1440, strict=True)
    day_two_priority_division_ids: list[str] = Field(default_factory=list)
    preferred_schedule_windows: list[PreferredScheduleWindow] = Field(default_factory=list)
    schedule_objective: Literal[
        "preserve_assignments", "compact_groups", "round_robin_first", "round_robin_batches"
    ] = "preserve_assignments"
    solver_time_limit_seconds: int = Field(default=60, gt=0, le=3600, strict=True)
    candidate_conflict_strategy: Literal["conservative"] = "conservative"
    import_max_file_mib: int = Field(default=25, gt=0, le=250, strict=True)
    import_max_expanded_mib: int = Field(default=250, gt=0, le=2500, strict=True)
    conversion_timeout_seconds: int = Field(default=60, gt=0, le=600, strict=True)


class ParserProfile(Model):
    source_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    layout: Literal["one_per_line", "horizontal_whitespace"] = "one_per_line"
    join_single_cjk_pairs: bool = Field(default=False, strict=True)
    ignored_paragraphs: list[Annotated[int, Field(strict=True, ge=0)]] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_indices(self) -> ParserProfile:
        if any(type(i) is not int or i < 0 for i in self.ignored_paragraphs):
            raise ValueError("Ignored paragraph indices must be nonnegative integers")
        return self


class RegistrationConfig(Model):
    source_files: list[dict[str, Any]] = Field(default_factory=list)
    division_aliases: dict[str, str | list[str]] = Field(default_factory=dict)
    identity_resolutions: list[dict[str, Any]] = Field(default_factory=list)
    parser_profiles: list[ParserProfile] = Field(default_factory=list)


class EventConfig(Model):
    schema_version: Literal["1.0.0"]
    document_kind: Literal["event_config_template", "event_config"]
    configuration_status: Literal["incomplete", "complete"]
    spec_id: Literal["SQUASH-SPEC-001"]
    policy_version: Literal["phase1-2026-10-27"]
    policy: dict[str, Any]
    event: Event
    registration: RegistrationConfig
    divisions_status: Literal["unconfigured", "configured"]
    divisions: list[Division]
    merge_whitelist_status: Literal["unconfigured", "configured"]
    merge_whitelist: list[dict[str, Any]]
    courts_status: Literal["unconfigured", "configured"]
    courts: list[Court]
    timing_defaults: Timing
    player_workload: PlayerWorkload = Field(default_factory=PlayerWorkload)
    match_rule_defaults: MatchRules
    draw_defaults: DrawConfig
    publication: Publication
    runtime: Runtime
    historical_review: dict[str, Any]
    rule_sets: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def approved_policy(self) -> EventConfig:
        expected = json.loads(
            files("squash_scheduler.assets").joinpath("policy.json").read_text("utf-8-sig")
        )
        if self.policy != expected:
            raise ValueError(
                "Configuration changes approved policy; explicit policy migration required"
            )
        for collection in (self.divisions, self.courts):
            if len({item.id for item in collection}) != len(collection):
                raise ValueError("Configuration entity IDs must be unique")
        if self.courts_status == "configured" and not self.courts:
            raise ValueError("Configured courts must not be empty")
        if self.divisions_status == "configured" and not self.divisions:
            raise ValueError("Configured divisions must not be empty")
        return self


def load_config(path: Path) -> dict[str, Any]:
    return EventConfig.model_validate_json(path.read_text("utf-8-sig")).model_dump(mode="json")
