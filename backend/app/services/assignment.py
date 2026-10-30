"""Recommendation runs and the concurrency-safe assignment path."""
from __future__ import annotations

import time as _time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_engine, new_locking_session, new_session
from ..geo import key as geokey
from ..models import Appointment, Assignment, AssignmentAttempt, Provider, RecommendationRun, Visit
from ..timeutil import now_ist
from .engine import EngineResult, VisitSpec, evaluate
from .routing import MatrixResult, RouteResult, RoutingProvider


class AssignmentError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def spec_from_visit(v: Visit) -> VisitSpec:
    return VisitSpec(service=v.service, mode=v.mode, lat=v.lat, lng=v.lng, start_at=v.start_at,
                     duration_minutes=v.duration_minutes, parent_id=v.parent_id, locality=v.locality,
                     required_capabilities=list(v.required_capabilities or []), visit_id=v.id)


def recommend(db: Session, visit: Visit, routing: RoutingProvider, now: datetime | None = None) -> tuple[RecommendationRun, EngineResult]:
    """Run the engine and persist the run. Never creates an assignment."""
    now = now or now_ist()
    t0 = _time.perf_counter()
    res = evaluate(db, spec_from_visit(visit), routing, now)
    latency = int((_time.perf_counter() - t0) * 1000)
    run = RecommendationRun(visit_id=visit.id, visit_version=visit.version, latency_ms=latency,
                            providers_evaluated=res.evaluated, feasible_count=res.feasible_count,
                            exclusion_counts=res.exclusion_counts, routing=res.routing, candidates=res.candidates,
                            outcome=res.outcome)
    db.add(run)
    db.commit()
    return run, res


# --- routing wrappers so the locked section never waits on the network ---------
class RecordingRouting:
    def __init__(self, inner: RoutingProvider):
        self.inner, self.cache = inner, {}
        self.name, self.mode, self.label = inner.name, inner.mode, inner.label

    def matrix(self, origins, destinations, depart_at=None) -> MatrixResult:
        m = self.inner.matrix(origins, destinations, depart_at)
        for i, o in enumerate(origins):
            for j, d in enumerate(destinations):
                self.cache[(geokey(*o), geokey(*d))] = m.grid[i][j] if m.grid else None
        return m


class ReplayRouting:
    def __init__(self, source: RecordingRouting):
        self.cache = source.cache
        self.name, self.mode, self.label = source.name, source.mode, source.label
        self.missed = False

    def matrix(self, origins, destinations, depart_at=None) -> MatrixResult:
        grid: list[list[RouteResult | None]] = []
        for o in origins:
            row = []
            for d in destinations:
                k = (geokey(*o), geokey(*d))
                if k not in self.cache:
                    self.missed = True
                row.append(self.cache.get(k))
            grid.append(row)
        got = sum(1 for r in grid for c in r if c)
        total = len(origins) * len(destinations)
        return MatrixResult(grid, "ok" if got == total else ("failed" if got == 0 else "partial"))


def _record_attempt(**kw) -> None:
    db = new_session()
    try:
        db.add(AssignmentAttempt(**kw))
        db.commit()
    finally:
        db.close()


def assign(visit_id: int, provider_id: str, run_id: int, assigned_by: str, override_reason: str | None,
           routing: RoutingProvider, now: datetime | None = None) -> dict:
    now = now or now_ist()
    attempt = dict(visit_id=visit_id, run_id=run_id, provider_id=provider_id, override_reason=override_reason,
                   routing_mode=routing.mode)
    try:
        # Phase 1: read-only checks and route lookups, outside the write lock.
        db = new_session()
        try:
            visit = db.get(Visit, visit_id)
            run = db.get(RecommendationRun, run_id)
            if visit is None:
                raise AssignmentError("VISIT_NOT_FOUND", "Visit not found", 404)
            if run is None or run.visit_id != visit_id:
                raise AssignmentError("RUN_NOT_FOUND", "Recommendation not found for this visit. Run Find providers first.", 404)
            if run.visit_version != visit.version:
                raise AssignmentError("RECOMMENDATION_STALE", "The visit changed after these recommendations. Run Find providers again.")
            listed = [c["provider_id"] for c in run.candidates]
            if provider_id not in listed:
                raise AssignmentError("NOT_RECOMMENDED", "That provider was not in the recommended shortlist.", 422)
            rank = listed.index(provider_id) + 1
            attempt.update(selected_rank=rank, chose_top=rank == 1)
            spec = spec_from_visit(visit)
            if spec.start_at < now:
                raise AssignmentError("VISIT_IN_PAST", "This visit starts in the past.", 422)
            rec = RecordingRouting(routing)
            evaluate(db, spec, rec, now, [provider_id])
        finally:
            db.close()

        # Phase 2: take the write lock, re-run every check against the freshest data, then write.
        db = new_locking_session()
        try:
            visit = db.get(Visit, visit_id)
            if visit.status == "cancelled":
                raise AssignmentError("VISIT_CANCELLED", "This visit was cancelled.")
            if visit.version != run.visit_version:
                raise AssignmentError("RECOMMENDATION_STALE", "The visit changed after these recommendations.")
            if get_engine().dialect.name != "sqlite":
                db.execute(select(Provider.id).where(Provider.id == provider_id).with_for_update())
            old = next((a for a in visit.assignments if a.status == "active"), None)
            if old and old.provider_id == provider_id:
                raise AssignmentError("ALREADY_ASSIGNED", "This provider is already assigned to the visit.")
            replay = ReplayRouting(rec)
            res = evaluate(db, spec_from_visit(visit), replay, now, [provider_id])
            if not res.candidates:
                if replay.missed:
                    raise AssignmentError("SCHEDULE_CHANGED", "This provider's schedule changed since the recommendation. Run Find providers again.")
                why = res.excluded[0] if res.excluded else {"code": "NOT_FEASIBLE", "reason": "No longer feasible", "detail": ""}
                raise AssignmentError(why["code"], f"{why['reason']}. {why['detail']}".strip(". ") + ". Pick another provider or run Find providers again.")
            cand = res.candidates[0]
            if old:
                old.status, old.ended_at, old.ended_reason = "superseded", now, f"Reassigned to {provider_id}"
                for a in db.scalars(select(Appointment).where(Appointment.visit_id == visit.id, Appointment.status == "scheduled")):
                    a.status = "cancelled"
            at_rec = next((c for c in run.candidates if c["provider_id"] == provider_id), {})
            breakdown = {**cand["components"], "_recommended_score": at_rec.get("score"), "_final_score": cand["score"], "_rank_note": at_rec.get("rank_note")}
            a = Assignment(visit_id=visit.id, provider_id=provider_id, run_id=run_id, assigned_by=assigned_by, assigned_at=now,
                           status="active", rank=rank, score=round(cand["score"], 1), breakdown=breakdown,
                           route=cand["route"], explanation=" | ".join(cand["reasons"]), override_reason=override_reason)
            db.add(a)
            db.add(Appointment(provider_id=provider_id, visit_id=visit.id, start_at=visit.start_at,
                               end_at=visit.start_at + (spec.end_at - spec.start_at),
                               lat=visit.lat if visit.mode == "in_person" else None,
                               lng=visit.lng if visit.mode == "in_person" else None,
                               locality=visit.locality if visit.mode == "in_person" else None, mode=visit.mode))
            visit.status = "assigned"
            db.add(AssignmentAttempt(success=True, **attempt))
            db.commit()
            db.refresh(a)
            return {"assignment_id": a.id, "visit_id": visit.id, "provider_id": provider_id, "status": "assigned",
                    "score": a.score, "rank": rank, "notification": "Simulated: no SMS or WhatsApp message was sent."}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except AssignmentError as e:
        _record_attempt(success=False, failure_code=e.code, **attempt)
        raise


def unassign(db: Session, visit_id: int, reason: str | None, now: datetime | None = None) -> Visit:
    now = now or now_ist()
    visit = db.get(Visit, visit_id)
    if visit is None:
        raise AssignmentError("VISIT_NOT_FOUND", "Visit not found", 404)
    cur = next((a for a in visit.assignments if a.status == "active"), None)
    if cur is None:
        raise AssignmentError("NOT_ASSIGNED", "This visit has no active assignment.")
    cur.status, cur.ended_at, cur.ended_reason = "cancelled", now, reason or "Unassigned by coordinator"
    for a in db.scalars(select(Appointment).where(Appointment.visit_id == visit.id, Appointment.status == "scheduled")):
        a.status = "cancelled"
    visit.status = "unassigned"
    db.commit()
    return visit
