"""Database schema. All datetimes are naive wall-clock times in Asia/Kolkata."""
from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    from .timeutil import now_ist

    return now_ist()


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # PRV-001
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(32), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    verification_status: Mapped[str] = mapped_column(String(16), default="verified")  # verified|pending|rejected
    credential_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    credential_expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    service_types: Mapped[list] = mapped_column(JSON, default=list)
    service_modes: Mapped[list] = mapped_column(JSON, default=list)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    localities: Mapped[list] = mapped_column(JSON, default=list)
    home_locality: Mapped[str] = mapped_column(String(48), index=True)
    service_radius_km: Mapped[float] = mapped_column(Float, default=10.0)
    base_lat: Mapped[float] = mapped_column(Float)
    base_lng: Mapped[float] = mapped_column(Float)
    work_days: Mapped[list] = mapped_column(JSON, default=list)  # 0=Mon .. 6=Sun
    work_start: Mapped[time] = mapped_column()
    work_end: Mapped[time] = mapped_column()
    default_visit_minutes: Mapped[int] = mapped_column(Integer, default=45)
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=15)
    max_daily_visits: Mapped[int] = mapped_column(Integer, default=5)
    max_daily_minutes: Mapped[int] = mapped_column(Integer, default=360)
    on_call_response_minutes: Mapped[int] = mapped_column(Integer, default=15)
    completed_visits: Mapped[int] = mapped_column(Integer, default=0)
    on_time_rate: Mapped[float] = mapped_column(Float, default=0.0)
    cancellations: Mapped[int] = mapped_column(Integer, default=0)
    offers_received: Mapped[int] = mapped_column(Integer, default=0)
    offers_accepted: Mapped[int] = mapped_column(Integer, default=0)
    locality_visits: Mapped[dict] = mapped_column(JSON, default=dict)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)

    leave_blocks: Mapped[list["LeaveBlock"]] = relationship(back_populates="provider", cascade="all, delete-orphan")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="provider", cascade="all, delete-orphan")


class LeaveBlock(Base):
    __tablename__ = "leave_blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    reason: Mapped[str] = mapped_column(String(120), default="Leave")

    provider: Mapped[Provider] = relationship(back_populates="leave_blocks")


class Parent(Base):
    __tablename__ = "parents"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # PAR-001
    name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(255))
    locality: Mapped[str] = mapped_column(String(48))
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)


class ParentProviderHistory(Base):
    """Completed past visits between a demo parent and a provider (for continuity)."""

    __tablename__ = "parent_provider_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[str] = mapped_column(ForeignKey("parents.id"), index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"), index=True)
    completed_visits: Mapped[int] = mapped_column(Integer, default=1)
    last_visit_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class Visit(Base):
    __tablename__ = "visits"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[str] = mapped_column(ForeignKey("parents.id"), index=True)
    service: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16))
    address: Mapped[str] = mapped_column(String(255))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    locality: Mapped[str | None] = mapped_column(String(48), nullable=True)
    geocode_source: Mapped[str] = mapped_column(String(16), default="demo")  # demo|ola|manual
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    required_capabilities: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="unassigned", index=True)  # unassigned|assigned|cancelled
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    parent: Mapped[Parent] = relationship()
    assignments: Mapped[list["Assignment"]] = relationship(back_populates="visit", order_by="Assignment.id")


class Appointment(Base):
    """A block on a provider's calendar. Linked to a visit when created by Smart Assign."""

    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"), index=True)
    visit_id: Mapped[int | None] = mapped_column(ForeignKey("visits.id"), nullable=True, index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    locality: Mapped[str | None] = mapped_column(String(48), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="in_person")
    status: Mapped[str] = mapped_column(String(16), default="scheduled")  # scheduled|cancelled

    provider: Mapped[Provider] = relationship(back_populates="appointments")


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), index=True)
    visit_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    providers_evaluated: Mapped[int] = mapped_column(Integer, default=0)
    feasible_count: Mapped[int] = mapped_column(Integer, default=0)
    exclusion_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    routing: Mapped[dict] = mapped_column(JSON, default=dict)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    outcome: Mapped[str] = mapped_column(String(24), default="ok")  # ok|no_feasible_provider|routing_failed


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("recommendation_runs.id"), nullable=True)
    assigned_by: Mapped[str] = mapped_column(String(80))
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|superseded|cancelled
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    route: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    visit: Mapped[Visit] = relationship(back_populates="assignments")
    provider: Mapped[Provider] = relationship()


class AssignmentAttempt(Base):
    """Every confirm click, successful or not, for the evaluation dashboard."""

    __tablename__ = "assignment_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    visit_id: Mapped[int] = mapped_column(Integer, index=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_id: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    success: Mapped[bool] = mapped_column(Boolean)
    failure_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    selected_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chose_top: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    override_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    routing_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
