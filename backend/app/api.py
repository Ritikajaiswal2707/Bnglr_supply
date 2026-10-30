"""REST API. The recommendation endpoint never creates an assignment; only /assign does."""
from __future__ import annotations

import hmac
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import seed as seed_mod
from .config import CAPABILITY_LABELS, LOCALITIES, MODE_LABELS, POLICY, ROLE_CAPABILITIES, ROLE_CREDENTIAL, ROLES, SERVICES, TIMEZONE_NAME
from .db import get_db
from .models import Appointment, Assignment, LeaveBlock, Parent, Provider, RecommendationRun, Visit
from .schemas import (AssignRequest, GeocodeRequest, GeocodeResponse, LeaveCreate, ProviderPatch, RecommendationResponse,
                      UnassignRequest, VisitBase, VisitCreate, VisitPatch)
from .services import analytics
from .services.assignment import AssignmentError, assign, recommend, spec_from_visit, unassign
from .services.engine import evaluate
from .timeutil import combine, hhmm, iso, now_ist, to_ist_naive

public = APIRouter()


# --------------------------------------------------------------------------- #
def require_token(request: Request, x_access_token: str | None = Header(default=None)) -> None:
    expected = request.app.state.settings.access_token
    if expected and not (x_access_token and hmac.compare_digest(x_access_token, expected)):
        raise HTTPException(401, "Access passcode required")


protected = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


def _routing(request: Request):
    return request.app.state.routing


def _now() -> datetime:
    return now_ist()


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #
def visit_out(v: Visit) -> dict:
    svc = SERVICES[v.service]
    cur = next((a for a in v.assignments if a.status == "active"), None)
    return {
        "id": v.id, "parent_id": v.parent_id, "parent_name": v.parent.name if v.parent else None,
        "service": v.service, "service_label": svc["label"], "role": svc["role"], "mode": v.mode,
        "address": v.address, "locality": v.locality, "lat": v.lat, "lng": v.lng, "geocode_source": v.geocode_source,
        "start_at": iso(v.start_at), "end_at": iso(v.start_at + timedelta(minutes=v.duration_minutes)),
        "duration_minutes": v.duration_minutes, "required_capabilities": v.required_capabilities or [],
        "status": v.status, "version": v.version, "created_at": iso(v.created_at), "updated_at": iso(v.updated_at),
        "assignment": None if cur is None else {
            "id": cur.id, "provider_id": cur.provider_id, "provider_name": cur.provider.name, "status": cur.status,
            "assigned_by": cur.assigned_by, "assigned_at": iso(cur.assigned_at), "score": cur.score, "rank": cur.rank},
    }


def credential_status(p: Provider, today) -> str:
    if not ROLE_CREDENTIAL.get(p.role):
        return "not_required"
    if not p.credential_expiry:
        return "missing"
    if p.credential_expiry < today:
        return "expired"
    return "expiring_soon" if (p.credential_expiry - today).days <= POLICY.credential_expiry_warning_days else "valid"


def provider_out(p: Provider, upcoming: int = 0, today=None) -> dict:
    today = today or _now().date()
    return {
        "id": p.id, "name": p.name, "role": p.role, "active": p.active, "verification_status": p.verification_status,
        "home_locality": p.home_locality, "localities": p.localities, "service_types": p.service_types,
        "service_labels": [SERVICES[s]["label"] for s in p.service_types if s in SERVICES],
        "service_modes": p.service_modes, "capabilities": p.capabilities,
        "credential_type": p.credential_type, "credential_expiry": p.credential_expiry.isoformat() if p.credential_expiry else None,
        "credential_status": credential_status(p, today),
        "work_days": p.work_days, "work_start": hhmm(p.work_start), "work_end": hhmm(p.work_end),
        "buffer_minutes": p.buffer_minutes, "max_daily_visits": p.max_daily_visits, "max_daily_minutes": p.max_daily_minutes,
        "service_radius_km": p.service_radius_km, "on_call_response_minutes": p.on_call_response_minutes,
        "completed_visits": p.completed_visits, "on_time_rate": p.on_time_rate, "cancellations": p.cancellations,
        "offers_received": p.offers_received, "offers_accepted": p.offers_accepted,
        "upcoming_visits": upcoming, "is_demo": p.is_demo,
        # Exact home coordinates are never exposed; only an approximate (~1 km) base.
        "base_approx": {"lat": round(p.base_lat, 2), "lng": round(p.base_lng, 2)},
    }


def _get_visit(db: Session, visit_id: int) -> Visit:
    v = db.get(Visit, visit_id)
    if v is None:
        raise HTTPException(404, "Visit not found")
    return v


def _err(e: AssignmentError) -> HTTPException:
    return HTTPException(e.status, detail={"code": e.code, "message": e.message})


# --------------------------------------------------------------------------- #
# Public
# --------------------------------------------------------------------------- #
@public.get("/health", tags=["system"])
def health(request: Request, db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "routing_mode": request.app.state.routing.mode, "time": iso(_now())}


@public.get("/api/config", tags=["system"])
def config(request: Request):
    s, r = request.app.state.settings, request.app.state.routing
    return {
        "app": "PeakAge Smart Assign", "tagline": "Find the right care provider for every visit.",
        "demo_data": True, "timezone": TIMEZONE_NAME, "now": iso(_now()), "auth_required": bool(s.access_token),
        "routing": {"configured_mode": s.routing_mode, "live_configured": s.live_configured, "mode": r.mode,
                    "label": r.label, "traffic_aware": False},
        "weights": POLICY.weights,
        "policy": {"max_travel_minutes": POLICY.max_travel_minutes, "travel_band_minutes": POLICY.travel_band_minutes,
                   "shortlist_size": POLICY.shortlist_size, "reliability_min_sample": POLICY.reliability_min_sample},
        "services": [{"key": k, "label": v["label"], "role": v["role"], "modes": v["modes"], "minutes": v["minutes"],
                      "capabilities": [{"key": c, "label": CAPABILITY_LABELS[c]} for c in ROLE_CAPABILITIES[v["role"]]]}
                     for k, v in SERVICES.items()],
        "mode_labels": MODE_LABELS, "roles": ROLES, "localities": [{"name": k, "lat": v[0], "lng": v[1]} for k, v in sorted(LOCALITIES.items())],
    }


# --------------------------------------------------------------------------- #
# Geocoding and parents
# --------------------------------------------------------------------------- #
@protected.post("/geocode", response_model=GeocodeResponse, tags=["geocoding"])
def geocode(body: GeocodeRequest, request: Request):
    res = request.app.state.geocoder.geocode(body.query)
    return GeocodeResponse(status=res.status, provider=res.provider, candidates=[c.as_dict() for c in res.candidates],
                           error=res.error, routing_mode_label=_routing(request).label)


@protected.get("/parents", tags=["parents"])
def parents(db: Session = Depends(get_db)):
    return [{"id": p.id, "name": p.name, "address": p.address, "locality": p.locality, "lat": p.lat, "lng": p.lng}
            for p in db.scalars(select(Parent).order_by(Parent.id))]


# --------------------------------------------------------------------------- #
# Visits
# --------------------------------------------------------------------------- #
def _apply(v: Visit, b: VisitBase) -> None:
    v.parent_id, v.service, v.mode, v.address = b.parent_id, b.service, b.mode, b.address.strip()
    v.lat, v.lng, v.locality, v.geocode_source = b.lat, b.lng, b.locality, b.geocode_source
    v.start_at, v.duration_minutes = combine(b.date, b.start_time), b.duration_minutes
    v.required_capabilities = b.required_capabilities


@protected.post("/visits", status_code=201, tags=["visits"])
def create_visit(body: VisitCreate, db: Session = Depends(get_db)):
    if db.get(Parent, body.parent_id) is None:
        raise HTTPException(422, "Unknown parent")
    if combine(body.date, body.start_time) < _now():
        raise HTTPException(422, "The visit start time is in the past")
    v = Visit(status="unassigned")
    _apply(v, body)
    db.add(v)
    db.commit()
    return visit_out(v)


@protected.get("/visits", tags=["visits"])
def list_visits(status: str | None = None, db: Session = Depends(get_db)):
    q = select(Visit).order_by(Visit.start_at)
    if status:
        q = q.where(Visit.status == status)
    return [visit_out(v) for v in db.scalars(q)]


@protected.get("/visits/{visit_id}", tags=["visits"])
def get_visit(visit_id: int, db: Session = Depends(get_db)):
    return visit_out(_get_visit(db, visit_id))


@protected.patch("/visits/{visit_id}", tags=["visits"])
def patch_visit(visit_id: int, body: VisitPatch, db: Session = Depends(get_db)):
    v = _get_visit(db, visit_id)
    if v.status != "unassigned":
        raise HTTPException(409, "Only unassigned visits can be edited")
    cur = {"parent_id": v.parent_id, "service": v.service, "mode": v.mode, "address": v.address, "lat": v.lat, "lng": v.lng,
           "locality": v.locality, "geocode_source": v.geocode_source, "date": v.start_at.date(), "start_time": v.start_at.time(),
           "duration_minutes": v.duration_minutes, "required_capabilities": v.required_capabilities or []}
    cur.update(body.model_dump(exclude_unset=True))
    try:
        merged = VisitBase(**cur)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if combine(merged.date, merged.start_time) < _now():
        raise HTTPException(422, "The visit start time is in the past")
    _apply(v, merged)
    v.version += 1  # invalidates earlier recommendation runs
    db.commit()
    return visit_out(v)


@protected.post("/visits/{visit_id}/recommendations", response_model=RecommendationResponse, tags=["allocation"])
def recommendations(visit_id: int, request: Request, db: Session = Depends(get_db)):
    v = _get_visit(db, visit_id)
    if v.status == "cancelled":
        raise HTTPException(409, "This visit was cancelled")
    if v.start_at < _now():
        raise HTTPException(422, "The visit start time is in the past")
    run, res = recommend(db, v, _routing(request), _now())
    return RecommendationResponse(
        recommendation_id=run.id, visit_id=v.id, outcome=res.outcome, message=res.message, candidates=res.candidates,
        feasible_count=res.feasible_count, providers_evaluated=res.evaluated, exclusion_counts=res.exclusion_counts,
        excluded=res.excluded, routing=res.routing, latency_ms=run.latency_ms)


@protected.post("/visits/{visit_id}/assign", tags=["allocation"])
def assign_visit(visit_id: int, body: AssignRequest, request: Request):
    try:
        return assign(visit_id, body.provider_id, body.recommendation_id, body.assigned_by, body.override_reason,
                      _routing(request), _now())
    except AssignmentError as e:
        raise _err(e)


@protected.post("/visits/{visit_id}/unassign", tags=["allocation"])
def unassign_visit(visit_id: int, body: UnassignRequest, db: Session = Depends(get_db)):
    try:
        return visit_out(unassign(db, visit_id, body.reason, _now()))
    except AssignmentError as e:
        raise _err(e)


@protected.get("/visits/{visit_id}/assignment-history", tags=["allocation"])
def assignment_history(visit_id: int, db: Session = Depends(get_db)):
    v = _get_visit(db, visit_id)
    return [{"id": a.id, "provider_id": a.provider_id, "provider_name": a.provider.name, "status": a.status,
             "assigned_by": a.assigned_by, "assigned_at": iso(a.assigned_at), "ended_at": iso(a.ended_at),
             "ended_reason": a.ended_reason, "rank": a.rank, "score": a.score, "breakdown": a.breakdown, "route": a.route,
             "explanation": a.explanation, "override_reason": a.override_reason, "recommendation_id": a.run_id}
            for a in v.assignments]


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
@protected.get("/providers", tags=["providers"])
def list_providers(q: str | None = None, role: str | None = None, verification: str | None = None,
                   locality: str | None = None, active: bool | None = None, db: Session = Depends(get_db)):
    stmt = select(Provider).order_by(Provider.id)
    if role:
        stmt = stmt.where(Provider.role == role)
    if verification:
        stmt = stmt.where(Provider.verification_status == verification)
    if active is not None:
        stmt = stmt.where(Provider.active == active)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(func.lower(Provider.name).like(like) | func.lower(Provider.id).like(like))
    now = _now()
    counts = dict(db.execute(select(Appointment.provider_id, func.count()).where(
        Appointment.status == "scheduled", Appointment.start_at >= now, Appointment.start_at < now + timedelta(days=7)
    ).group_by(Appointment.provider_id)).all())
    out = []
    for p in db.scalars(stmt):
        if locality and locality not in (p.localities or []):
            continue
        out.append(provider_out(p, counts.get(p.id, 0), now.date()))
    return out


@protected.get("/providers/{provider_id}", tags=["providers"])
def get_provider(provider_id: str, db: Session = Depends(get_db)):
    p = db.get(Provider, provider_id)
    if p is None:
        raise HTTPException(404, "Provider not found")
    now = _now()
    appts = list(db.scalars(select(Appointment).where(
        Appointment.provider_id == p.id, Appointment.status == "scheduled", Appointment.start_at >= now - timedelta(hours=12),
        Appointment.start_at < now + timedelta(days=8)).order_by(Appointment.start_at)))
    out = provider_out(p, len(appts), now.date())
    out["appointments"] = [{"id": a.id, "visit_id": a.visit_id, "start_at": iso(a.start_at), "end_at": iso(a.end_at),
                            "locality": a.locality, "mode": a.mode, "service_label": None}
                           for a in appts]
    vs = {v.id: v for v in db.scalars(select(Visit).where(Visit.id.in_([a.visit_id for a in appts if a.visit_id])))} if appts else {}
    for row, a in zip(out["appointments"], appts):
        v = vs.get(a.visit_id)
        row["service_label"] = SERVICES[v.service]["label"] if v else None
    out["leave_blocks"] = [{"id": lb.id, "start_at": iso(lb.start_at), "end_at": iso(lb.end_at), "reason": lb.reason}
                           for lb in sorted(p.leave_blocks, key=lambda x: x.start_at) if lb.end_at > now]
    out["locality_visits"] = p.locality_visits
    return out


@protected.patch("/providers/{provider_id}", tags=["providers"])
def patch_provider(provider_id: str, body: ProviderPatch, db: Session = Depends(get_db)):
    p = db.get(Provider, provider_id)
    if p is None:
        raise HTTPException(404, "Provider not found")
    for k, val in body.model_dump(exclude_unset=True).items():
        setattr(p, k, val)
    if p.work_end <= p.work_start:
        raise HTTPException(422, "Shift end must be after shift start")
    db.commit()
    return provider_out(p, 0)


@protected.post("/providers/{provider_id}/leave", status_code=201, tags=["providers"])
def add_leave(provider_id: str, body: LeaveCreate, db: Session = Depends(get_db)):
    if db.get(Provider, provider_id) is None:
        raise HTTPException(404, "Provider not found")
    lb = LeaveBlock(provider_id=provider_id, start_at=to_ist_naive(body.start_at), end_at=to_ist_naive(body.end_at), reason=body.reason)
    db.add(lb)
    db.commit()
    return {"id": lb.id, "start_at": iso(lb.start_at), "end_at": iso(lb.end_at), "reason": lb.reason}


@protected.delete("/providers/{provider_id}/leave/{leave_id}", status_code=204, tags=["providers"])
def delete_leave(provider_id: str, leave_id: int, db: Session = Depends(get_db)):
    lb = db.get(LeaveBlock, leave_id)
    if lb is None or lb.provider_id != provider_id:
        raise HTTPException(404, "Leave block not found")
    db.delete(lb)
    db.commit()


@protected.get("/providers/{provider_id}/eligibility", tags=["providers"])
def eligibility(provider_id: str, request: Request, visit_id: int = Query(...), db: Session = Depends(get_db)):
    """Why is this provider (not) recommendable for this visit? For debugging the engine."""
    if db.get(Provider, provider_id) is None:
        raise HTTPException(404, "Provider not found")
    v = _get_visit(db, visit_id)
    res = evaluate(db, spec_from_visit(v), _routing(request), _now(), [provider_id])
    if res.candidates:
        return {"eligible": True, "candidate": res.candidates[0], "exclusion": None}
    return {"eligible": False, "candidate": None, "exclusion": res.excluded[0] if res.excluded else None}


# --------------------------------------------------------------------------- #
# Analytics and admin
# --------------------------------------------------------------------------- #
@protected.get("/analytics/summary", tags=["analytics"])
def analytics_summary(db: Session = Depends(get_db)):
    return analytics.summary(db)


@protected.post("/admin/reset-demo", tags=["admin"])
def reset_demo():
    """Drop all data and restore the original synthetic dataset."""
    return {"status": "reset", **seed_mod.reset_demo_data()}

