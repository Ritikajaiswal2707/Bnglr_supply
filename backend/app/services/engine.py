"""Deterministic, explainable provider allocation.

Stage A  hard eligibility filters (no routing needed)
Stage B  schedule and travel feasibility (routing for survivors only)
Stage C  weighted 0-100 ranking of feasible providers
Stage D  shortlist with explanations

No LLM is involved. Every exclusion carries a structured reason code. A provider that
fails any hard rule can never appear in the shortlist, whatever their score.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import MODE_LABELS, POLICY, ROLE_CREDENTIAL, SERVICES, Policy
from ..geo import haversine_km, key as geokey
from ..models import Appointment, LeaveBlock, ParentProviderHistory, Provider
from ..timeutil import combine, day_bounds, hhmm, iso, minutes_between
from .routing import LatLng, RouteResult, RoutingProvider

EXCLUSIONS = {
    "INACTIVE": "Provider inactive",
    "ROLE_MISMATCH": "Role mismatch",
    "SERVICE_NOT_APPROVED": "Not approved for this service",
    "VERIFICATION_INCOMPLETE": "Verification incomplete",
    "CREDENTIAL_MISSING": "Required credential missing",
    "CREDENTIAL_EXPIRED": "Required credential expired",
    "MODE_UNSUPPORTED": "Service mode not supported",
    "CAPABILITY_MISSING": "Required capability missing",
    "OUTSIDE_COVERAGE": "Outside service coverage",
    "OUTSIDE_WORKING_HOURS": "Outside working hours",
    "WORKING_HOURS_EXCEEDED": "Visit runs past working hours",
    "ON_LEAVE": "On leave or blocked",
    "APPOINTMENT_CONFLICT": "Existing appointment conflict",
    "WORKLOAD_LIMIT": "Workload limit reached",
    "ON_CALL_RESPONSE_SLOW": "On-call response time too slow",
    "ROUTE_UNAVAILABLE": "Route could not be calculated",
    "TRAVEL_TOO_LONG": "Travel time above limit",
    "INSUFFICIENT_TRAVEL_BUFFER": "Insufficient travel buffer",
}

FACTOR_LABELS = {
    "travel": "travel time",
    "continuity": "continuity of care",
    "reliability": "reliability",
    "schedule_fit": "schedule fit",
    "locality": "locality fit",
    "workload": "workload balance",
}


@dataclass
class VisitSpec:
    service: str
    mode: str
    lat: float
    lng: float
    start_at: datetime  # naive IST
    duration_minutes: int
    parent_id: str
    locality: str | None = None
    required_capabilities: list[str] = field(default_factory=list)
    visit_id: int | None = None  # the visit's own calendar block is ignored

    @property
    def end_at(self) -> datetime:
        return self.start_at + timedelta(minutes=self.duration_minutes)

    @property
    def dest(self) -> LatLng:
        return (self.lat, self.lng)


@dataclass
class EngineResult:
    candidates: list[dict]
    excluded: list[dict]
    exclusion_counts: dict
    evaluated: int
    feasible_count: int
    routing: dict
    outcome: str  # ok | no_feasible_provider | routing_failed
    message: str | None = None


def _ex(p: Provider, code: str, detail: str) -> dict:
    return {
        "provider_id": p.id,
        "name": p.name,
        "role": p.role,
        "code": code,
        "reason": EXCLUSIONS[code],
        "detail": detail,
    }


def _overlaps(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> bool:
    return a0 < b1 and b0 < a1


# --------------------------------------------------------------------------- #
# Scoring components (each returns 0-100 plus a short evidence string)
# --------------------------------------------------------------------------- #
def travel_score(minutes: float, pol: Policy = POLICY) -> float:
    band = pol.travel_band_minutes
    banded = round(minutes / band) * band
    if banded <= pol.travel_full_score_minutes:
        return 100.0
    span = pol.max_travel_minutes - pol.travel_full_score_minutes
    return max(0.0, 100.0 * (1 - (banded - pol.travel_full_score_minutes) / span))


def reliability_score(p: Provider, pol: Policy = POLICY) -> tuple[float, dict]:
    k = pol.prior_strength
    n = max(p.completed_visits, 0)
    on_time = (p.on_time_rate * n + pol.prior_on_time_rate * k) / (n + k)
    accept = (p.offers_accepted + pol.prior_acceptance_rate * k) / (p.offers_received + k)
    cancel = (p.cancellations + pol.prior_cancellation_rate * k) / (n + p.cancellations + k)
    w_on, w_acc, w_can = pol.reliability_mix
    score = 100.0 * (w_on * on_time + w_acc * accept + w_can * (1 - cancel))
    limited = n < pol.reliability_min_sample
    return score, {
        "completed_visits": n,
        "on_time_rate_raw": round(p.on_time_rate, 3) if n else None,
        "on_time_rate_adjusted": round(on_time, 3),
        "cancellations": p.cancellations,
        "acceptance_rate_adjusted": round(accept, 3),
        "evidence": "limited" if limited else "sufficient",
    }


def continuity_score(prior_visits: int, service: str, pol: Policy = POLICY) -> tuple[float, str]:
    if not SERVICES[service]["continuity"]:
        return pol.continuity_neutral, "Continuity is not a factor for this service"
    if prior_visits <= 0:
        return pol.continuity_neutral, "No prior visits with this parent"
    n = min(prior_visits, pol.continuity_cap_visits)
    score = pol.continuity_neutral + (100 - pol.continuity_neutral) * n / pol.continuity_cap_visits
    return score, f"{prior_visits} prior visit{'s' if prior_visits != 1 else ''} with this parent"


def slack_score(slack_minutes: float | None, pol: Policy = POLICY) -> float:
    if slack_minutes is None:
        return 100.0
    return max(0.0, min(slack_minutes, pol.slack_full_score_minutes)) / pol.slack_full_score_minutes * 100.0


def locality_score(p: Provider, locality: str | None, within_radius: bool, pol: Policy = POLICY) -> tuple[float, str]:
    if locality and locality in (p.localities or []):
        base, text = 1.0, f"Regularly covers {locality}"
    elif within_radius:
        base, text = 0.5, "Within service radius"
    else:
        base, text = 0.0, "Outside usual localities"
    hist = min((p.locality_visits or {}).get(locality or "", 0), pol.locality_history_cap) / pol.locality_history_cap
    if hist > 0:
        text += f", {(p.locality_visits or {}).get(locality, 0)} past visits there"
    return 100.0 * (0.6 * base + 0.4 * hist), text


def workload_score(day_visits: int, week_visits: int, p: Provider) -> float:
    day_ratio = day_visits / max(p.max_daily_visits, 1)
    week_ratio = week_visits / max(p.max_daily_visits * 5, 1)
    return max(0.0, 100.0 * (1 - (0.7 * day_ratio + 0.3 * week_ratio)))


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
@dataclass
class Context:
    providers: list[Provider]
    appts: dict[str, list[Appointment]]
    leaves: dict[str, list[LeaveBlock]]
    history: dict[str, int]


def load_context(db: Session, spec: VisitSpec, provider_ids: list[str] | None = None) -> Context:
    q = select(Provider).order_by(Provider.id)
    if provider_ids:
        q = q.where(Provider.id.in_(provider_ids))
    providers = list(db.scalars(q))
    day0, _ = day_bounds(spec.start_at.date())
    week_end = day0 + timedelta(days=7)
    appts: dict[str, list[Appointment]] = defaultdict(list)
    aq = select(Appointment).where(
        Appointment.status == "scheduled", Appointment.start_at >= day0, Appointment.start_at < week_end
    )
    for a in db.scalars(aq):
        if spec.visit_id is not None and a.visit_id == spec.visit_id:
            continue
        appts[a.provider_id].append(a)
    leaves: dict[str, list[LeaveBlock]] = defaultdict(list)
    for lb in db.scalars(select(LeaveBlock).where(LeaveBlock.end_at > day0, LeaveBlock.start_at < week_end)):
        leaves[lb.provider_id].append(lb)
    hist = {
        h.provider_id: h.completed_visits
        for h in db.scalars(select(ParentProviderHistory).where(ParentProviderHistory.parent_id == spec.parent_id))
    }
    return Context(providers, appts, leaves, hist)


# --------------------------------------------------------------------------- #
# Stage A
# --------------------------------------------------------------------------- #
def stage_a(p: Provider, spec: VisitSpec, ctx: Context, pol: Policy = POLICY) -> tuple[str, str] | None:
    svc = SERVICES[spec.service]
    if not p.active:
        return "INACTIVE", "Marked inactive"
    if p.role != svc["role"]:
        return "ROLE_MISMATCH", f"{p.role}, service needs {svc['role']}"
    if spec.service not in (p.service_types or []):
        return "SERVICE_NOT_APPROVED", f"Not approved for {svc['label']}"
    if p.verification_status != "verified":
        return "VERIFICATION_INCOMPLETE", f"Verification is {p.verification_status}"
    if ROLE_CREDENTIAL.get(p.role):
        if not p.credential_expiry:
            return "CREDENTIAL_MISSING", "No credential on file"
        if p.credential_expiry < spec.start_at.date():
            return "CREDENTIAL_EXPIRED", f"Expired on {p.credential_expiry.isoformat()}"
    if spec.mode not in (p.service_modes or []):
        return "MODE_UNSUPPORTED", f"Does not offer {MODE_LABELS[spec.mode].lower()} visits"
    missing = [c for c in spec.required_capabilities if c not in (p.capabilities or [])]
    if missing:
        return "CAPABILITY_MISSING", "Lacks " + ", ".join(missing)
    if spec.mode == "in_person":
        within = haversine_km(p.base_lat, p.base_lng, spec.lat, spec.lng) <= p.service_radius_km
        if not ((spec.locality and spec.locality in (p.localities or [])) or within):
            return "OUTSIDE_COVERAGE", f"{spec.locality or 'This location'} is outside their localities and radius"
    d = spec.start_at.date()
    if spec.start_at.weekday() not in (p.work_days or []):
        return "OUTSIDE_WORKING_HOURS", "Does not work on this day"
    ws, we = combine(d, p.work_start), combine(d, p.work_end)
    if spec.start_at < ws:
        return "OUTSIDE_WORKING_HOURS", f"Starts work at {hhmm(p.work_start)}"
    if spec.end_at > we:
        return "WORKING_HOURS_EXCEEDED", f"Visit ends {spec.end_at:%H:%M}, shift ends {hhmm(p.work_end)}"
    for lb in ctx.leaves.get(p.id, []):
        if _overlaps(spec.start_at, spec.end_at, lb.start_at, lb.end_at):
            return "ON_LEAVE", lb.reason or "Leave"
    today = [a for a in ctx.appts.get(p.id, []) if a.start_at.date() == d]
    for a in today:
        if _overlaps(spec.start_at, spec.end_at, a.start_at, a.end_at):
            return "APPOINTMENT_CONFLICT", f"Busy {a.start_at:%H:%M} to {a.end_at:%H:%M}"
    booked = sum(minutes_between(a.start_at, a.end_at) for a in today)
    if len(today) + 1 > p.max_daily_visits or booked + spec.duration_minutes > p.max_daily_minutes:
        return "WORKLOAD_LIMIT", f"{len(today)} visits already booked that day"
    if spec.mode == "on_call" and p.on_call_response_minutes > pol.max_on_call_response_minutes:
        return "ON_CALL_RESPONSE_SLOW", f"Responds in about {p.on_call_response_minutes} min"
    return None


# --------------------------------------------------------------------------- #
# Stage B + C helpers
# --------------------------------------------------------------------------- #
def _neighbours(p: Provider, spec: VisitSpec, ctx: Context):
    today = sorted((a for a in ctx.appts.get(p.id, []) if a.start_at.date() == spec.start_at.date()), key=lambda a: a.start_at)
    prev = max((a for a in today if a.end_at <= spec.start_at), key=lambda a: a.end_at, default=None)
    nxt = min((a for a in today if a.start_at >= spec.end_at), key=lambda a: a.start_at, default=None)
    return today, prev, nxt


def _first_origin(p: Provider, prev: Appointment | None, spec: VisitSpec, now: datetime):
    """(origin LatLng, kind, departure time). Falls back to the base when the previous
    appointment has no coordinates (such as an on-call block)."""
    if prev is not None and prev.lat is not None and prev.mode == "in_person":
        return (prev.lat, prev.lng), "previous_visit", prev.end_at
    d = spec.start_at.date()
    depart = max(combine(d, p.work_start), now)
    if prev is not None:
        depart = max(depart, prev.end_at)
    return (p.base_lat, p.base_lng), "home_base", depart


def _approx(lat: float, lng: float) -> dict:
    return {"lat": round(lat, 2), "lng": round(lng, 2)}


def evaluate(
    db: Session,
    spec: VisitSpec,
    routing: RoutingProvider,
    now: datetime,
    provider_ids: list[str] | None = None,
    pol: Policy = POLICY,
) -> EngineResult:
    ctx = load_context(db, spec, provider_ids)
    excluded: list[dict] = []
    survivors: list[Provider] = []
    for p in ctx.providers:
        hit = stage_a(p, spec, ctx, pol)
        if hit:
            excluded.append(_ex(p, *hit))
        else:
            survivors.append(p)

    routing_meta = {"provider": routing.name, "mode": routing.mode, "label": routing.label, "traffic_aware": False,
                    "status": "not_needed" if spec.mode == "on_call" else "ok", "error": None, "computed_at": None}

    feasible: list[dict] = []
    if spec.mode == "on_call":
        for p in survivors:
            feasible.append(_score_on_call(p, spec, ctx, pol))
    elif survivors:
        feasible, routing_meta = _stage_b_in_person(survivors, spec, ctx, routing, now, pol, excluded, routing_meta)

    # Stage C ranking with deterministic tie-break
    def sort_key(c):
        return (-round(c["score"], 2), c["travel_minutes"] if c["travel_minutes"] is not None else 0, c["workload"]["visits_that_day"], c["provider_id"])

    feasible.sort(key=sort_key)
    for i, c in enumerate(feasible, 1):
        c["rank"] = i
    _add_rank_notes(feasible)

    counts: dict[str, int] = defaultdict(int)
    for e in excluded:
        counts[e["code"]] += 1

    if routing_meta["status"] == "failed" and not feasible:
        outcome = "routing_failed"
    else:
        outcome = "ok" if feasible else "no_feasible_provider"
    message = _message(outcome, feasible, excluded, counts, spec, routing_meta, pol)
    return EngineResult(feasible[: pol.shortlist_size] if pol.shortlist_size else feasible, excluded, dict(counts),
                        len(ctx.providers), len(feasible), routing_meta, outcome, message)


def _stage_b_in_person(survivors, spec, ctx, routing, now, pol, excluded, meta):
    infos = {}
    for p in survivors:
        _, prev, nxt = _neighbours(p, spec, ctx)
        origin, kind, depart = _first_origin(p, prev, spec, now)
        infos[p.id] = (prev, nxt, origin, kind, depart)

    # Leg 1: every distinct origin -> the parent. One batched matrix call.
    origins, seen = [], {}
    for p in survivors:
        o = infos[p.id][2]
        if geokey(*o) not in seen:
            seen[geokey(*o)] = len(origins)
            origins.append(o)
    # Departure time only matters to demo routing's peak-hour factor; use the earliest window start.
    depart_hint = min(i[4] for i in infos.values())
    m1 = routing.matrix(origins, [spec.dest], depart_hint)
    meta["status"] = m1.status
    meta["error"] = m1.error
    meta["computed_at"] = next((c.computed_at for r in m1.grid for c in r if c), None)

    # Leg 2: the parent -> each provider's next appointment (only to verify the visit does not run them late).
    nxt_locs, nseen = [], {}
    for p in survivors:
        nxt = infos[p.id][1]
        if nxt is not None and nxt.lat is not None and nxt.mode == "in_person":
            k = geokey(nxt.lat, nxt.lng)
            if k not in nseen:
                nseen[k] = len(nxt_locs)
                nxt_locs.append((nxt.lat, nxt.lng))
    m2 = routing.matrix([spec.dest], nxt_locs, spec.end_at) if nxt_locs else None

    feasible = []
    for p in survivors:
        prev, nxt, origin, kind, depart = infos[p.id]
        r1: RouteResult | None = m1.grid[seen[geokey(*origin)]][0] if m1.grid else None
        if r1 is None:
            excluded.append(_ex(p, "ROUTE_UNAVAILABLE", "Travel feasibility could not be verified"))
            continue
        if r1.duration_min > pol.max_travel_minutes:
            excluded.append(_ex(p, "TRAVEL_TOO_LONG", f"{round(r1.duration_min)} min travel, limit {pol.max_travel_minutes}"))
            continue
        window = minutes_between(depart, spec.start_at)
        need = r1.duration_min + p.buffer_minutes
        if need > window:
            src = "previous visit" if kind == "previous_visit" else "start of shift"
            excluded.append(_ex(p, "INSUFFICIENT_TRAVEL_BUFFER",
                                f"Needs {round(need)} min (travel {round(r1.duration_min)} + buffer {p.buffer_minutes}), only {max(round(window), 0)} min after {src}"))
            continue
        slack_prev = window - need
        warnings: list[str] = []
        slack_next = None
        if nxt is not None:
            gap = minutes_between(spec.end_at, nxt.start_at)
            r2 = None
            if m2 is not None and nxt.lat is not None and nxt.mode == "in_person":
                r2 = m2.grid[0][nseen[geokey(nxt.lat, nxt.lng)]]
            if r2 is not None:
                slack_next = gap - (r2.duration_min + p.buffer_minutes)
                if slack_next < 0:
                    excluded.append(_ex(p, "INSUFFICIENT_TRAVEL_BUFFER",
                                        f"Would leave {max(round(gap), 0)} min to reach their {nxt.start_at:%H:%M} appointment, needs {round(r2.duration_min + p.buffer_minutes)}"))
                    continue
            elif nxt.mode == "in_person" and nxt.lat is not None:
                warnings.append(f"Travel to their {nxt.start_at:%H:%M} appointment could not be verified")
            else:
                slack_next = gap - pol.on_call_gap_minutes
                if slack_next < 0:
                    excluded.append(_ex(p, "INSUFFICIENT_TRAVEL_BUFFER", "No gap before their next on-call block"))
                    continue
        feasible.append(_score_in_person(p, spec, ctx, r1, prev, nxt, origin, kind, depart, slack_prev, slack_next, warnings, pol))
    return feasible, meta


# --------------------------------------------------------------------------- #
# Candidate assembly
# --------------------------------------------------------------------------- #
def _common(p: Provider, spec: VisitSpec, ctx: Context, pol: Policy):
    d = spec.start_at.date()
    mine = ctx.appts.get(p.id, [])
    day_visits = sum(1 for a in mine if a.start_at.date() == d)
    week_visits = len(mine)
    rel, rel_info = reliability_score(p, pol)
    prior = ctx.history.get(p.id, 0)
    cont, cont_note = continuity_score(prior, spec.service, pol)
    return day_visits, week_visits, rel, rel_info, prior, cont, cont_note


def _assemble(p, spec, ctx, comps: dict, applicable: dict, notes: dict, pol, extra: dict) -> dict:
    weights = pol.weights
    total_w = sum(weights[k] for k in weights if applicable[k])
    components, score = {}, 0.0
    for k, w in weights.items():
        eff = (w / total_w) if applicable[k] else 0.0
        s = round(comps[k], 1)
        components[k] = {"label": FACTOR_LABELS[k], "score": s, "weight": w, "effective_weight": round(eff, 4),
                         "applicable": applicable[k], "note": notes[k], "points": round(eff * comps[k], 2)}
        score += eff * comps[k]
    return {"provider_id": p.id, "name": p.name, "role": p.role, "verification_status": p.verification_status,
            "home_locality": p.home_locality, "score": round(min(max(score, 0.0), 100.0), 4), "components": components,
            "mode": spec.mode, **extra}


def _warnings_common(p, spec, rel_info, pol, slack, rt: RoutingProvider | None = None) -> list[str]:
    w = []
    if rel_info["evidence"] == "limited":
        w.append(f"Limited history ({rel_info['completed_visits']} completed visits), reliability score is pulled towards neutral")
    if p.credential_expiry and 0 <= (p.credential_expiry - spec.start_at.date()).days <= pol.credential_expiry_warning_days:
        w.append(f"Credential expires on {p.credential_expiry.isoformat()}")
    if slack is not None and slack < pol.tight_slack_warning_minutes:
        w.append(f"Tight schedule: only {max(round(slack), 0)} min of slack")
    return w


def _score_in_person(p, spec, ctx, r1, prev, nxt, origin, kind, depart, slack_prev, slack_next, warnings, pol) -> dict:
    day_visits, week_visits, rel, rel_info, prior, cont, cont_note = _common(p, spec, ctx, pol)
    slack_vals = [s for s in (slack_prev if kind == "previous_visit" else None, slack_next) if s is not None]
    min_slack = min(slack_vals) if slack_vals else None
    within = haversine_km(p.base_lat, p.base_lng, spec.lat, spec.lng) <= p.service_radius_km
    loc, loc_note = locality_score(p, spec.locality, within, pol)
    comps = {"travel": travel_score(r1.duration_min, pol), "continuity": cont, "reliability": rel,
             "schedule_fit": slack_score(min_slack, pol), "locality": loc,
             "workload": workload_score(day_visits, week_visits, p)}
    notes = {
        "travel": f"{round(r1.duration_min)} min, {r1.distance_km:.1f} km by road",
        "continuity": cont_note,
        "reliability": f"{rel_info['completed_visits']} visits, on-time {round(rel_info['on_time_rate_adjusted'] * 100)}% (adjusted), evidence {rel_info['evidence']}",
        "schedule_fit": "No adjacent bookings" if min_slack is None else f"{max(round(min_slack), 0)} min of slack",
        "locality": loc_note,
        "workload": f"{day_visits} visit{'s' if day_visits != 1 else ''} that day, {week_visits} in the next 7 days",
    }
    warnings = _warnings_common(p, spec, rel_info, pol, min_slack) + warnings
    if r1.mode == "simulated":
        warnings.append("ETA is simulated, not live traffic")
    arrival = depart + timedelta(minutes=r1.duration_min)
    if kind == "previous_visit":
        origin_info = {"kind": kind, "label": f"After their {prev.start_at:%H:%M} visit in {prev.locality or 'Bengaluru'}", "lat": origin[0], "lng": origin[1]}
    else:
        origin_info = {"kind": kind, "label": f"From their base near {p.home_locality} (approximate)", **_approx(*origin)}
    c = _assemble(p, spec, ctx, comps, {k: True for k in comps}, notes, pol, {
        "route": r1.as_dict(),
        "origin": origin_info,
        "travel_minutes": round(r1.duration_min),
        "distance_km": round(r1.distance_km, 1),
        "straight_line_km": round(haversine_km(origin[0], origin[1], spec.lat, spec.lng), 1),
        "straight_line_label": "Approximate straight-line distance",
        "departs_at": iso(depart),
        "earliest_arrival": iso(arrival),
        "slack_minutes": None if min_slack is None else round(min_slack),
        "availability": "Free at the requested time",
        "workload": {"visits_that_day": day_visits, "visits_next_7_days": week_visits, "max_daily_visits": p.max_daily_visits},
        "previous_visits_with_parent": prior,
        "reliability": rel_info,
        "warnings": warnings,
    })
    c["reasons"] = _reasons(c, p)
    return c


def _score_on_call(p, spec, ctx, pol) -> dict:
    day_visits, week_visits, rel, rel_info, prior, cont, cont_note = _common(p, spec, ctx, pol)
    d = spec.start_at.date()
    today = [a for a in ctx.appts.get(p.id, []) if a.start_at.date() == d]
    gaps = [minutes_between(a.end_at, spec.start_at) for a in today if a.end_at <= spec.start_at]
    gaps += [minutes_between(spec.end_at, a.start_at) for a in today if a.start_at >= spec.end_at]
    slack = min((g - pol.on_call_gap_minutes for g in gaps), default=None)
    if slack is not None and slack < 0:
        slack = 0.0
    comps = {"travel": 100.0, "continuity": cont, "reliability": rel, "schedule_fit": slack_score(slack, pol),
             "locality": 100.0, "workload": workload_score(day_visits, week_visits, p)}
    applicable = {"travel": False, "continuity": True, "reliability": True, "schedule_fit": True, "locality": False, "workload": True}
    notes = {
        "travel": "Not applicable, remote consultation",
        "continuity": cont_note,
        "reliability": f"{rel_info['completed_visits']} visits, on-time {round(rel_info['on_time_rate_adjusted'] * 100)}% (adjusted), evidence {rel_info['evidence']}",
        "schedule_fit": "No adjacent bookings" if slack is None else f"{round(slack)} min of slack",
        "locality": "Not applicable, remote consultation",
        "workload": f"{day_visits} visit{'s' if day_visits != 1 else ''} that day, {week_visits} in the next 7 days",
    }
    c = _assemble(p, spec, ctx, comps, applicable, notes, pol, {
        "route": None,
        "origin": {"kind": "none", "label": "Remote, no travel", "lat": None, "lng": None},
        "travel_minutes": None, "distance_km": None, "straight_line_km": None, "straight_line_label": None,
        "departs_at": None, "earliest_arrival": None,
        "slack_minutes": None if slack is None else round(slack),
        "availability": f"Free, responds in about {p.on_call_response_minutes} min",
        "workload": {"visits_that_day": day_visits, "visits_next_7_days": week_visits, "max_daily_visits": p.max_daily_visits},
        "previous_visits_with_parent": prior,
        "reliability": rel_info,
        "warnings": _warnings_common(p, spec, rel_info, pol, slack),
    })
    c["reasons"] = _reasons(c, p)
    return c


# --------------------------------------------------------------------------- #
# Explanations
# --------------------------------------------------------------------------- #
def _reasons(c: dict, p: Provider) -> list[str]:
    out = []
    comp = c["components"]
    if c["mode"] == "on_call":
        out.append(f"Available for an on-call session, usually responds in about {p.on_call_response_minutes} min")
    else:
        origin = "after their previous visit" if c["origin"]["kind"] == "previous_visit" else "from their base"
        out.append(f"{c['travel_minutes']} min by road ({c['distance_km']} km) {origin}, arriving about {c['earliest_arrival'][11:16]}")
    if c["previous_visits_with_parent"] and comp["continuity"]["score"] > 50:
        out.append(f"Has visited this parent {c['previous_visits_with_parent']} time{'s' if c['previous_visits_with_parent'] != 1 else ''} before")
    rel = c["reliability"]
    if rel["evidence"] == "sufficient":
        out.append(f"{rel['completed_visits']} completed visits, on-time rate about {round(rel['on_time_rate_adjusted'] * 100)}%")
    else:
        out.append("Reliability evidence is limited, so that score stays close to neutral")
    if c["mode"] == "in_person" and comp["locality"]["score"] >= 60:
        out.append(comp["locality"]["note"])
    wl = c["workload"]
    out.append(f"{wl['visits_that_day']} visit{'s' if wl['visits_that_day'] != 1 else ''} already booked that day")
    return out[:4]


def _add_rank_notes(cands: list[dict]) -> None:
    if not cands:
        return
    best = cands[0]
    best["rank_note"] = "Highest overall score"
    for c in cands[1:]:
        deltas = {k: best["components"][k]["points"] - c["components"][k]["points"] for k in c["components"]}
        gap = best["score"] - c["score"]
        top = max(deltas, key=lambda k: deltas[k])
        if deltas[top] <= 0.05:
            c["rank_note"] = f"{gap:.1f} points behind the best match, split across several factors"
        else:
            c["rank_note"] = f"{gap:.1f} points behind the best match, mostly on {FACTOR_LABELS[top]} ({c['components'][top]['note']})"


def _message(outcome, feasible, excluded, counts, spec, meta, pol) -> str | None:
    if outcome == "routing_failed":
        return f"Travel times could not be calculated: {meta.get('error') or 'routing unavailable'}. No providers were recommended, because travel feasibility cannot be verified."
    if outcome == "no_feasible_provider":
        relevant = {k: v for k, v in counts.items() if k != "ROLE_MISMATCH"}
        if not relevant:
            return f"No providers hold the {SERVICES[spec.service]['role']} role in this dataset."
        top = sorted(relevant.items(), key=lambda kv: -kv[1])[:3]
        parts = ", ".join(f"{EXCLUSIONS[k].lower()} ({v})" for k, v in top)
        return f"No feasible provider found. Most common blockers: {parts}. Try a different time, a shorter visit, or another service mode."
    if len(feasible) < pol.shortlist_size:
        n = len(feasible)
        return f"Only {n} provider{'s' if n != 1 else ''} passed every check, so the shortlist has fewer than {pol.shortlist_size}."
    return None
