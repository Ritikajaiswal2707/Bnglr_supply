"""API integration: the recommend/assign contract, stale recommendations, double booking, concurrency."""
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import new_session
from app.main import create_app
from app.models import Appointment, Parent
from app.services.assignment import AssignmentError, assign
from app.services.routing import DemoRoutingProvider
from app.timeutil import now_ist
from .conftest import INDIRANAGAR, mk_provider


def day(offset=3):
    return str((now_ist() + timedelta(days=offset)).date())


@pytest.fixture()
def client(tmp_path):
    s = Settings()
    s.database_url, s.routing_mode, s.access_token = f"sqlite:///{tmp_path}/api.db", "demo", ""
    with TestClient(create_app(s, auto_seed=False)) as c:
        db = new_session()
        for i in (1, 2):
            db.add(Parent(id=f"PAR-00{i}", name=f"Parent {i} (demo)", address="1 Test Road, Indiranagar", locality="Indiranagar",
                          lat=INDIRANAGAR[0], lng=INDIRANAGAR[1]))
        db.commit()
        c.db = db
        yield c
        db.close()


def visit(c, start="11:00", offset=3, parent="PAR-001", service="vitals_check", mode="in_person", minutes=45, **kw):
    body = dict(parent_id=parent, service=service, mode=mode, address="1 Test Road, Indiranagar, Bengaluru", lat=INDIRANAGAR[0],
                lng=INDIRANAGAR[1], locality="Indiranagar", geocode_source="manual", date=day(offset), start_time=start,
                duration_minutes=minutes)
    body.update(kw)
    return c.post("/api/visits", json=body)


def recommend(c, vid):
    r = c.post(f"/api/visits/{vid}/recommendations")
    assert r.status_code == 200, r.text
    return r.json()


def test_health_and_config_hide_secrets(client):
    assert client.get("/health").json()["status"] == "ok"
    cfg = client.get("/api/config").json()
    assert cfg["routing"]["mode"] == "simulated" and "Demo routing" in cfg["routing"]["label"]
    assert "api_key" not in str(cfg).lower() and cfg["weights"]["travel"] == 0.30


def test_recommendation_never_assigns_then_assign_updates_schedule(client):
    p = mk_provider(client.db)
    vid = visit(client).json()["id"]
    rec = recommend(client, vid)
    assert rec["outcome"] == "ok" and rec["candidates"][0]["provider_id"] == p.id
    assert client.get(f"/api/visits/{vid}").json()["status"] == "unassigned"
    assert client.get(f"/api/providers/{p.id}").json()["appointments"] == []

    r = client.post(f"/api/visits/{vid}/assign", json={"provider_id": p.id, "recommendation_id": rec["recommendation_id"]})
    assert r.status_code == 200 and r.json()["notification"].startswith("Simulated")
    v = client.get(f"/api/visits/{vid}").json()
    assert v["status"] == "assigned" and v["assignment"]["provider_id"] == p.id
    assert len(client.get(f"/api/providers/{p.id}").json()["appointments"]) == 1
    h = client.get(f"/api/visits/{vid}/assignment-history").json()
    assert h[0]["status"] == "active" and h[0]["breakdown"]["travel"]["score"] >= 0 and h[0]["route"]["mode"] == "simulated"

    # The provider is now busy: an overlapping visit no longer recommends them.
    v2 = visit(client, start="11:15", parent="PAR-002").json()["id"]
    rec2 = recommend(client, v2)
    assert rec2["candidates"] == [] and rec2["exclusion_counts"]["APPOINTMENT_CONFLICT"] == 1


def test_provider_becomes_unavailable_after_ranking_is_rejected(client):
    p = mk_provider(client.db)
    vid = visit(client).json()["id"]
    rec = recommend(client, vid)
    start = f"{day()}T10:00:00+05:30"
    assert client.post(f"/api/providers/{p.id}/leave", json={"start_at": start, "end_at": f"{day()}T14:00:00+05:30", "reason": "Sick"}).status_code == 201
    r = client.post(f"/api/visits/{vid}/assign", json={"provider_id": p.id, "recommendation_id": rec["recommendation_id"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ON_LEAVE"
    assert client.get(f"/api/visits/{vid}").json()["status"] == "unassigned"
    assert client.get("/api/analytics/summary").json()["assignment_failure_rate"] == 100.0


def test_double_booking_after_ranking_is_rejected(client):
    p = mk_provider(client.db)
    a, b = visit(client).json()["id"], visit(client, start="11:15", parent="PAR-002").json()["id"]
    ra, rb = recommend(client, a), recommend(client, b)
    assert client.post(f"/api/visits/{a}/assign", json={"provider_id": p.id, "recommendation_id": ra["recommendation_id"]}).status_code == 200
    r = client.post(f"/api/visits/{b}/assign", json={"provider_id": p.id, "recommendation_id": rb["recommendation_id"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "APPOINTMENT_CONFLICT"


@pytest.mark.parametrize("round_", range(4))
def test_two_coordinators_cannot_book_the_same_provider_concurrently(client, round_):
    p = mk_provider(client.db)
    a = visit(client, offset=3 + round_).json()["id"]
    b = visit(client, offset=3 + round_, start="11:15", parent="PAR-002").json()["id"]
    ra, rb = recommend(client, a), recommend(client, b)
    barrier, out = threading.Barrier(2), []

    def go(vid, rec):
        barrier.wait()
        try:
            out.append(("ok", assign(vid, p.id, rec["recommendation_id"], "coordinator", None, DemoRoutingProvider(), now_ist())))
        except AssignmentError as e:
            out.append(("err", e.code))

    ts = [threading.Thread(target=go, args=(a, ra)), threading.Thread(target=go, args=(b, rb))]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(k for k, _ in out) == ["err", "ok"], out
    assert [v for k, v in out if k == "err"] == ["APPOINTMENT_CONFLICT"]
    from sqlalchemy import func, select
    n = new_session().scalar(select(func.count(Appointment.id)).where(Appointment.provider_id == p.id, Appointment.status == "scheduled"))
    assert n == 1


def test_visit_edit_invalidates_old_recommendation(client):
    p = mk_provider(client.db)
    vid = visit(client).json()["id"]
    rec = recommend(client, vid)
    assert client.patch(f"/api/visits/{vid}", json={"start_time": "12:00"}).status_code == 200
    r = client.post(f"/api/visits/{vid}/assign", json={"provider_id": p.id, "recommendation_id": rec["recommendation_id"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "RECOMMENDATION_STALE"


def test_only_recommended_providers_can_be_assigned(client):
    mk_provider(client.db, "PRV-001")
    other = mk_provider(client.db, "PRV-002", verification_status="pending")
    vid = visit(client).json()["id"]
    rec = recommend(client, vid)
    r = client.post(f"/api/visits/{vid}/assign", json={"provider_id": other.id, "recommendation_id": rec["recommendation_id"]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "NOT_RECOMMENDED"
    assert client.get(f"/api/providers/{other.id}/eligibility", params={"visit_id": vid}).json()["exclusion"]["code"] == "VERIFICATION_INCOMPLETE"


def test_reassignment_keeps_history_and_frees_old_provider(client):
    a, b = mk_provider(client.db, "PRV-001"), mk_provider(client.db, "PRV-002")
    vid = visit(client).json()["id"]
    rec = recommend(client, vid)
    first = rec["candidates"][0]["provider_id"]
    second = rec["candidates"][1]["provider_id"]
    client.post(f"/api/visits/{vid}/assign", json={"provider_id": first, "recommendation_id": rec["recommendation_id"]})
    r = client.post(f"/api/visits/{vid}/assign", json={"provider_id": second, "recommendation_id": rec["recommendation_id"],
                                                         "override_reason": "Prefers this provider"})
    assert r.status_code == 200
    hist = client.get(f"/api/visits/{vid}/assignment-history").json()
    assert [h["status"] for h in hist] == ["superseded", "active"] and hist[1]["override_reason"] == "Prefers this provider"
    assert client.get(f"/api/providers/{first}").json()["appointments"] == []
    s = client.get("/api/analytics/summary").json()
    assert s["assignments_confirmed"] == 2 and s["top_ranked_selection_rate"] == 50.0
    assert client.post(f"/api/visits/{vid}/unassign", json={}).json()["status"] == "unassigned"
    assert client.get(f"/api/providers/{second}").json()["appointments"] == []


def test_validation(client):
    assert visit(client, offset=-2).status_code == 422                                       # in the past
    assert visit(client, lat=19.07, lng=72.87).status_code == 422                            # not Bengaluru
    assert visit(client, service="nurse_x").status_code == 422                               # unknown service
    assert visit(client, mode="on_call").status_code == 422                                  # nurse visits are in person
    assert visit(client, minutes=5).status_code == 422
    assert visit(client, parent="PAR-999").status_code == 422
    assert client.post("/api/geocode", json={"query": "ab"}).status_code == 422
    assert client.get("/api/visits/9999").status_code == 404


def test_geocode_endpoint_demo_mode_is_labelled(client):
    g = client.post("/api/geocode", json={"query": "5th Cross, HSR Layout"}).json()
    assert g["status"] == "ok" and g["candidates"][0]["approximate"] is True and "Demo routing" in g["routing_mode_label"]
    assert client.post("/api/geocode", json={"query": "nowhere in particular"}).json()["status"] == "no_match"


def test_provider_directory_filters_and_privacy(client):
    mk_provider(client.db, "PRV-001")
    mk_provider(client.db, "PRV-002", role="Doctor", verification_status="pending")
    assert [p["id"] for p in client.get("/api/providers", params={"role": "Doctor"}).json()] == ["PRV-002"]
    assert [p["id"] for p in client.get("/api/providers", params={"verification": "verified"}).json()] == ["PRV-001"]
    assert len(client.get("/api/providers", params={"q": "prv-002"}).json()) == 1
    assert client.get("/api/providers", params={"locality": "Whitefield"}).json() == []
    detail = client.get("/api/providers/PRV-001").json()
    assert "base_lat" not in detail and detail["base_approx"] == {"lat": 12.97, "lng": 77.64}
    r = client.patch("/api/providers/PRV-001", json={"active": False, "work_days": [0, 1]})
    assert r.status_code == 200 and r.json()["active"] is False
    assert client.patch("/api/providers/PRV-001", json={"work_start": "18:00", "work_end": "09:00"}).status_code == 422


def test_access_passcode_gate(tmp_path):
    s = Settings()
    s.database_url, s.routing_mode, s.access_token = f"sqlite:///{tmp_path}/x.db", "demo", "letmein"
    with TestClient(create_app(s, auto_seed=False)) as c:
        assert c.get("/health").status_code == 200 and c.get("/api/config").json()["auth_required"] is True
        assert c.get("/api/providers").status_code == 401
        assert c.get("/api/providers", headers={"X-Access-Token": "wrong"}).status_code == 401
        assert c.get("/api/providers", headers={"X-Access-Token": "letmein"}).status_code == 200


def test_reset_demo_restores_dataset(client):
    r = client.post("/api/admin/reset-demo").json()
    assert r["status"] == "reset" and 50 <= r["providers"] <= 100
    assert len(client.get("/api/providers").json()) == r["providers"]
    assert {p["role"] for p in client.get("/api/providers").json()} == {"Nurse", "Concierge", "Doctor", "Dietitian", "Fitness Coach", "Counsellor"}
