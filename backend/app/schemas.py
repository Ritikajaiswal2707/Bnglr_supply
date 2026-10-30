"""Pydantic request and response schemas."""
from __future__ import annotations

import datetime as dt
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .config import ROLE_CAPABILITIES, SERVICES
from .geo import in_bengaluru

Mode = Literal["in_person", "on_call"]


class GeocodeRequest(BaseModel):
    query: str = Field(min_length=3, max_length=200)


class GeoCandidateOut(BaseModel):
    label: str
    lat: float
    lng: float
    locality: str | None
    source: str
    approximate: bool


class GeocodeResponse(BaseModel):
    status: Literal["ok", "no_match", "failed"]
    provider: str
    candidates: list[GeoCandidateOut]
    error: str | None = None
    routing_mode_label: str


class VisitBase(BaseModel):
    parent_id: str
    service: str
    mode: Mode
    address: str = Field(min_length=3, max_length=255)
    lat: float
    lng: float
    locality: str | None = None
    geocode_source: Literal["demo", "ola", "manual"] = "demo"
    date: dt.date
    start_time: dt.time
    duration_minutes: int = Field(ge=10, le=480)
    required_capabilities: list[str] = Field(default_factory=list)

    @field_validator("service")
    @classmethod
    def _service(cls, v: str) -> str:
        if v not in SERVICES:
            raise ValueError("Unknown service")
        return v

    @model_validator(mode="after")
    def _check(self):
        svc = SERVICES[self.service]
        if self.mode not in svc["modes"]:
            raise ValueError(f"{svc['label']} does not support {self.mode.replace('_', ' ')} visits")
        if not in_bengaluru(self.lat, self.lng):
            raise ValueError("Location is outside Bengaluru. This prototype covers Bengaluru only.")
        allowed = set(ROLE_CAPABILITIES[svc["role"]])
        bad = [c for c in self.required_capabilities if c not in allowed]
        if bad:
            raise ValueError("Unknown capability for this service: " + ", ".join(bad))
        return self


class VisitCreate(VisitBase):
    pass


class VisitPatch(BaseModel):
    service: str | None = None
    mode: Mode | None = None
    address: str | None = Field(default=None, min_length=3, max_length=255)
    lat: float | None = None
    lng: float | None = None
    locality: str | None = None
    geocode_source: Literal["demo", "ola", "manual"] | None = None
    date: dt.date | None = None
    start_time: dt.time | None = None
    duration_minutes: int | None = Field(default=None, ge=10, le=480)
    required_capabilities: list[str] | None = None


class AssignRequest(BaseModel):
    provider_id: str
    recommendation_id: int
    assigned_by: str = Field(default="City Coordinator", min_length=1, max_length=80)
    override_reason: str | None = Field(default=None, max_length=255)


class UnassignRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class ProviderPatch(BaseModel):
    active: bool | None = None
    verification_status: Literal["verified", "pending", "rejected"] | None = None
    work_days: list[int] | None = None
    work_start: dt.time | None = None
    work_end: dt.time | None = None
    buffer_minutes: int | None = Field(default=None, ge=0, le=120)
    max_daily_visits: int | None = Field(default=None, ge=1, le=12)
    credential_expiry: dt.date | None = None

    @field_validator("work_days")
    @classmethod
    def _days(cls, v):
        if v is not None and (any(d < 0 or d > 6 for d in v) or len(set(v)) != len(v)):
            raise ValueError("work_days must be unique numbers 0 (Mon) to 6 (Sun)")
        return sorted(v) if v is not None else v

    @model_validator(mode="after")
    def _hours(self):
        if self.work_start and self.work_end and self.work_end <= self.work_start:
            raise ValueError("Shift end must be after shift start")
        return self


class LeaveCreate(BaseModel):
    start_at: datetime
    end_at: datetime
    reason: str = Field(default="Leave", max_length=120)

    @model_validator(mode="after")
    def _order(self):
        if self.end_at <= self.start_at:
            raise ValueError("End must be after start")
        return self


class RoutingInfo(BaseModel):
    provider: str
    mode: str
    label: str
    traffic_aware: bool
    status: str
    error: str | None
    computed_at: str | None


class Candidate(BaseModel):
    rank: int
    provider_id: str
    name: str
    role: str
    verification_status: str
    home_locality: str
    mode: Mode
    score: float
    components: dict[str, Any]
    route: dict[str, Any] | None
    origin: dict[str, Any]
    travel_minutes: int | None
    distance_km: float | None
    straight_line_km: float | None
    straight_line_label: str | None
    departs_at: str | None
    earliest_arrival: str | None
    slack_minutes: int | None
    availability: str
    workload: dict[str, Any]
    previous_visits_with_parent: int
    reliability: dict[str, Any]
    warnings: list[str]
    reasons: list[str]
    rank_note: str


class Exclusion(BaseModel):
    provider_id: str
    name: str
    role: str
    code: str
    reason: str
    detail: str


class RecommendationResponse(BaseModel):
    recommendation_id: int
    visit_id: int
    outcome: Literal["ok", "no_feasible_provider", "routing_failed"]
    message: str | None
    candidates: list[Candidate]
    feasible_count: int
    providers_evaluated: int
    exclusion_counts: dict[str, int]
    excluded: list[Exclusion]
    routing: RoutingInfo
    latency_ms: int
