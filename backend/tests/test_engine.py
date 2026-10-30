"""Engine behaviour: hard filters, feasibility, ranking, explanations."""
from datetime import date, datetime, time, timedelta

import pytest

from app.config import DEFAULT_WEIGHTS, POLICY
from app.models import LeaveBlock, ParentProviderHistory
from app.services.engine import evaluate, travel_score
from .conftest import DAY, INDIRANAGAR, KORAMANGALA, NOW, StubRouting, mk_appt, mk_provider, mk_spec


def run(db, spec=None, routing=None, **kw):
    return evaluate(db, spec or mk_spec(), routing or StubRouting(), NOW, **kw)


def codes(res):
    return {e["provider_id"]: e["code"] for e in res.excluded}


# ---- 1, 12: nothing to recommend ------------------------------------------------
def test_no_providers_with_required_role(db):
    mk_provider(db, "PRV-001", role="Doctor")
    res = run(db)
    assert res.outcome == "no_feasible_provider" and res.candidates == []
    assert codes(res) == {"PRV-001": "ROLE_MISMATCH"}
    assert "No providers hold the Nurse role" in res.message


def test_no_feasible_provider_is_explicit_with_actionable_reasons(db):
    mk_provider(db, "PRV-001", verification_status="pending")
    mk_provider(db, "PRV-002", active=False)
    res = run(db)
    assert res.outcome == "no_feasible_provider"
    assert res.message.startswith("No feasible provider found")
    assert "verification incomplete" in res.message.lower()


# ---- 2, 3: verification and credentials ---------------------------------------
def test_all_unverified_are_excluded(db):
    for i, st in enumerate(["pending", "rejected"], 1):
        mk_provider(db, f"PRV-00{i}", verification_status=st)
    res = run(db)
    assert res.feasible_count == 0 and set(codes(res).values()) == {"VERIFICATION_INCOMPLETE"}


def test_expired_credential_excluded_even_if_expiring_before_visit_date(db):
    mk_provider(db, "PRV-001", credential_expiry=DAY - timedelta(days=1))
    mk_provider(db, "PRV-002", credential_expiry=DAY)  # still valid on the day
    mk_provider(db, "PRV-003", credential_expiry=None)
    res = run(db)
    assert codes(res)["PRV-001"] == "CREDENTIAL_EXPIRED"
    assert codes(res)["PRV-003"] == "CREDENTIAL_MISSING"
    assert [c["provider_id"] for c in res.candidates] == ["PRV-002"]
    assert any("Credential expires" in w for w in res.candidates[0]["warnings"])


def test_concierge_needs_no_credential(db):
    mk_provider(db, "PRV-001", role="Concierge")
    res = run(db, mk_spec(service="errand_support", minutes=60))
    assert [c["provider_id"] for c in res.candidates] == ["PRV-001"]


# ---- 4, 5, 23: availability, conflicts, working hours ----------------------------
def test_no_availability_on_requested_date(db):
    mk_provider(db, "PRV-001", work_days=[1, 2, 3])  # not Monday
    p2 = mk_provider(db, "PRV-002")
    db.add(LeaveBlock(provider_id=p2.id, start_at=datetime.combine(DAY, time(0, 0)), end_at=datetime.combine(DAY, time(23, 59)), reason="Leave"))
    db.commit()
    res = run(db)
    assert codes(res) == {"PRV-001": "OUTSIDE_WORKING_HOURS", "PRV-002": "ON_LEAVE"}


def test_overlapping_appointment_blocks(db):
    p = mk_provider(db)
    mk_appt(db, p, datetime.combine(DAY, time(10, 0)), 60)  # 10:00-11:00 overlaps 10:30-11:15
    assert codes(run(db)) == {"PRV-001": "APPOINTMENT_CONFLICT"}


def test_visit_longer_than_remaining_shift(db):
    mk_provider(db, work_end=time(18, 0))
    res = run(db, mk_spec(start=datetime.combine(DAY, time(17, 30)), minutes=60))
    assert codes(res) == {"PRV-001": "WORKING_HOURS_EXCEEDED"}
    early = run(db, mk_spec(start=datetime.combine(DAY, time(8, 0))))
    assert codes(early) == {"PRV-001": "OUTSIDE_WORKING_HOURS"}


def test_workload_cap(db):
    p = mk_provider(db, max_daily_visits=1)
    mk_appt(db, p, datetime.combine(DAY, time(12, 0)), 45)
    assert codes(run(db)) == {"PRV-001": "WORKLOAD_LIMIT"}


# ---- 6, 7, 21: travel feasibility --------------------------------------------
def test_preceding_appointment_makes_travel_infeasible(db):
    near = mk_provider(db, "PRV-001")
    far = mk_provider(db, "PRV-002")
    mk_appt(db, far, datetime.combine(DAY, time(9, 35)), 45)  # ends 10:20, window to 10:30 is 10 min
    res = run(db, routing=StubRouting(lambda o, d: 30.0))
    assert codes(res)["PRV-002"] == "INSUFFICIENT_TRAVEL_BUFFER"
    assert [c["provider_id"] for c in res.candidates] == ["PRV-001"]
    assert "after previous visit" in [e for e in res.excluded if e["provider_id"] == "PRV-002"][0]["detail"]


def test_origin_is_previous_appointment_not_home(db):
    p = mk_provider(db)
    mk_appt(db, p, datetime.combine(DAY, time(9, 0)), 45, loc=KORAMANGALA)  # ends 09:45
    seen = []
    def fn(o, d):
        seen.append(o)
        return 20.0
    res = run(db, routing=StubRouting(fn))
    assert res.candidates[0]["origin"]["kind"] == "previous_visit"
    assert (KORAMANGALA[0], KORAMANGALA[1]) in seen and (p.base_lat, p.base_lng) not in seen


def test_travel_buffer_and_travel_limit(db):
    mk_provider(db, "PRV-001")
    res = run(db, routing=StubRouting(lambda o, d: POLICY.max_travel_minutes + 20))
    assert codes(res) == {"PRV-001": "TRAVEL_TOO_LONG"}
    # 55 min travel + 15 buffer needs 70 min but the shift starts 09:00, visit 09:30 -> only 30 min window
    res = run(db, mk_spec(start=datetime.combine(DAY, time(9, 30))), StubRouting(lambda o, d: 55.0))
    assert codes(res) == {"PRV-001": "INSUFFICIENT_TRAVEL_BUFFER"}


def test_next_appointment_is_protected(db):
    p = mk_provider(db)
    mk_appt(db, p, datetime.combine(DAY, time(11, 30)), 45, loc=KORAMANGALA)  # visit ends 11:15
    res = run(db, routing=StubRouting(lambda o, d: 30.0))  # needs 30+15 but gap is 15
    assert codes(res) == {"PRV-001": "INSUFFICIENT_TRAVEL_BUFFER"}
    assert "11:30" in res.excluded[0]["detail"]


def test_no_valid_route_never_invents_a_distance(db):
    p = mk_provider(db)
    mk_appt(db, p, datetime.combine(DAY, time(9, 0)), 45, loc=KORAMANGALA)
    res = run(db, routing=StubRouting(lambda o, d: None))
    assert res.candidates == [] and codes(res) == {"PRV-001": "ROUTE_UNAVAILABLE"}


def test_whole_routing_failure_reports_routing_failed(db):
    mk_provider(db)
    res = run(db, routing=StubRouting(fail="Ola Maps timed out"))
    assert res.outcome == "routing_failed" and "cannot be verified" in res.message
    assert res.candidates == []


def test_partial_matrix_excludes_only_unrouted_providers(db):
    mk_provider(db, "PRV-001", base_lat=12.97, base_lng=77.64)
    mk_provider(db, "PRV-002", base_lat=12.99, base_lng=77.60)
    res = run(db, routing=StubRouting(lambda o, d: None if o[0] == 12.99 else 15.0))
    assert [c["provider_id"] for c in res.candidates] == ["PRV-001"]
    assert codes(res) == {"PRV-002": "ROUTE_UNAVAILABLE"}


# ---- 8, 9: reliability and continuity ----------------------------------------
def test_one_perfect_visit_does_not_outrank_experience(db):
    mk_provider(db, "PRV-001", completed_visits=1, on_time_rate=1.0, cancellations=0, offers_received=1, offers_accepted=1)
    mk_provider(db, "PRV-002", completed_visits=250, on_time_rate=0.93, cancellations=3, offers_received=270, offers_accepted=255)
    res = run(db)
    by = {c["provider_id"]: c for c in res.candidates}
    assert by["PRV-002"]["components"]["reliability"]["score"] > by["PRV-001"]["components"]["reliability"]["score"]
    assert by["PRV-001"]["reliability"]["evidence"] == "limited"
    assert any("Limited history" in w for w in by["PRV-001"]["warnings"])


def test_no_parent_history_is_neutral_and_history_helps(db):
    mk_provider(db, "PRV-001")
    mk_provider(db, "PRV-002")
    res = run(db)
    for c in res.candidates:
        assert c["components"]["continuity"]["score"] == POLICY.continuity_neutral
        assert "No prior visits" in c["components"]["continuity"]["note"]
    db.add(ParentProviderHistory(parent_id="PAR-001", provider_id="PRV-002", completed_visits=3))
    db.commit()
    res = run(db)
    assert res.candidates[0]["provider_id"] == "PRV-002"
    assert res.candidates[0]["previous_visits_with_parent"] == 3
    assert res.candidates[0]["components"]["continuity"]["score"] == 100


def test_continuity_never_overrides_eligibility(db):
    mk_provider(db, "PRV-001", verification_status="pending")
    mk_provider(db, "PRV-002")
    db.add(ParentProviderHistory(parent_id="PAR-001", provider_id="PRV-001", completed_visits=6))
    db.commit()
    assert [c["provider_id"] for c in run(db).candidates] == ["PRV-002"]


# ---- 10, 11: ties and short lists ---------------------------------------------
def test_ties_break_deterministically(db):
    for i in (3, 1, 2):
        mk_provider(db, f"PRV-00{i}")
    orders = {tuple(c["provider_id"] for c in run(db).candidates) for _ in range(5)}
    assert orders == {("PRV-001", "PRV-002", "PRV-003")}


def test_only_one_feasible_provider(db):
    mk_provider(db, "PRV-001")
    mk_provider(db, "PRV-002", active=False)
    res = run(db)
    assert len(res.candidates) == 1 and "Only 1 provider" in res.message


def test_shortlist_is_top_three_best_first(db):
    for i in range(1, 6):
        mk_provider(db, f"PRV-00{i}")
    res = run(db, routing=StubRouting(lambda o, d: 10.0 + 8 * (round(o[0] * 1000) % 5)))
    assert len(res.candidates) == 3 and res.feasible_count == 5
    scores = [c["score"] for c in res.candidates]
    assert scores == sorted(scores, reverse=True)
    assert [c["rank"] for c in res.candidates] == [1, 2, 3]


# ---- 22: on-call ---------------------------------------------------------------
def test_on_call_skips_travel_checks_and_renormalises_weights(db):
    mk_provider(db, "PRV-001", role="Doctor", service_modes=["in_person", "on_call"], localities=["Whitefield"], base_lat=12.8, base_lng=77.5)
    mk_provider(db, "PRV-002", role="Doctor", service_modes=["in_person"])
    routing = StubRouting(fail="should not be called")
    res = run(db, mk_spec(service="doctor_consult", mode="on_call", minutes=30), routing)
    assert [c["provider_id"] for c in res.candidates] == ["PRV-001"]
    assert routing.calls == 0 and res.routing["status"] == "not_needed"
    c = res.candidates[0]
    assert c["route"] is None and c["travel_minutes"] is None
    assert not c["components"]["travel"]["applicable"]
    assert sum(x["effective_weight"] for x in c["components"].values()) == pytest.approx(1.0, abs=1e-3)
    assert codes(res) == {"PRV-002": "MODE_UNSUPPORTED"}


def test_on_call_still_respects_overlaps_and_response_policy(db):
    a = mk_provider(db, "PRV-001", role="Doctor", service_modes=["on_call"])
    mk_provider(db, "PRV-002", role="Doctor", service_modes=["on_call"], on_call_response_minutes=45)
    mk_appt(db, a, datetime.combine(DAY, time(10, 15)), 30, mode="on_call")
    res = run(db, mk_spec(service="doctor_consult", mode="on_call", minutes=30))
    assert codes(res) == {"PRV-001": "APPOINTMENT_CONFLICT", "PRV-002": "ON_CALL_RESPONSE_SLOW"}


# ---- scoring -------------------------------------------------------------------
def test_weights_documented_and_sum_to_one():
    assert DEFAULT_WEIGHTS == {"travel": .30, "continuity": .20, "reliability": .20, "schedule_fit": .15, "locality": .10, "workload": .05}
    assert sum(DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)


def test_score_matches_formula_and_components_bounded(db):
    mk_provider(db)
    c = run(db).candidates[0]
    comp = c["components"]
    expected = sum(DEFAULT_WEIGHTS[k] * comp[k]["score"] for k in DEFAULT_WEIGHTS)
    assert c["score"] == pytest.approx(expected, abs=0.2)  # components are rounded to 1 dp in output
    assert all(0 <= v["score"] <= 100 for v in comp.values()) and 0 <= c["score"] <= 100


def test_travel_score_monotonic_and_banded():
    vals = [travel_score(m) for m in range(0, 80)]
    assert all(a >= b for a, b in zip(vals, vals[1:]))
    assert travel_score(21) == travel_score(22)  # same 5 minute band, uncertain ETAs are not over-read
    assert travel_score(5) == 100 and travel_score(POLICY.max_travel_minutes) == 0


def test_closer_provider_ranks_first_and_explains_gap(db):
    mk_provider(db, "PRV-001", base_lat=12.9785, base_lng=77.6409)
    mk_provider(db, "PRV-002", base_lat=12.93, base_lng=77.60)
    res = run(db, routing=StubRouting(lambda o, d: 8.0 if o[0] > 12.97 else 40.0))
    assert [c["provider_id"] for c in res.candidates] == ["PRV-001", "PRV-002"]
    assert "travel time" in res.candidates[1]["rank_note"]
    assert res.candidates[0]["rank_note"] == "Highest overall score"


def test_recommendation_does_not_touch_the_schedule(db):
    from sqlalchemy import func, select
    from app.models import Appointment
    mk_provider(db)
    before = db.scalar(select(func.count(Appointment.id)))
    run(db)
    assert db.scalar(select(func.count(Appointment.id))) == before


def test_disqualified_provider_never_recommended_regardless_of_score(db):
    mk_provider(db, "PRV-001", verification_status="rejected", completed_visits=500, on_time_rate=0.99)
    mk_provider(db, "PRV-002", completed_visits=2, on_time_rate=0.5)
    res = run(db)
    assert [c["provider_id"] for c in res.candidates] == ["PRV-002"]


def test_capability_requirement(db):
    mk_provider(db, "PRV-001", capabilities=["wound_care"])
    mk_provider(db, "PRV-002", capabilities=[])
    res = run(db, mk_spec(required_capabilities=["wound_care"]))
    assert [c["provider_id"] for c in res.candidates] == ["PRV-001"] and codes(res) == {"PRV-002": "CAPABILITY_MISSING"}


def test_outside_coverage(db):
    mk_provider(db, "PRV-001", localities=["Whitefield"], base_lat=12.97, base_lng=77.75, service_radius_km=5)
    assert codes(run(db)) == {"PRV-001": "OUTSIDE_COVERAGE"}
