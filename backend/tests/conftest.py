from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from app.config import SERVICES
from app.db import Base, get_engine, init_engine, new_session
from app.geo import haversine_km
from app.models import Appointment, Parent, ParentProviderHistory, Provider
from app.services.engine import VisitSpec
from app.services.routing import MatrixResult, RouteResult

NOW = datetime(2026, 10, 30, 9, 0)          # a Friday morning
DAY = date(2026, 11, 2)                      # the following Monday
INDIRANAGAR = (12.9784, 77.6408)
KORAMANGALA = (12.9352, 77.6245)
WHITEFIELD = (12.9698, 77.7500)


@pytest.fixture()
def db(tmp_path):
    init_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(get_engine())
    s = new_session()
    s.add(Parent(id="PAR-001", name="Test Parent (demo)", address="1 Test Road, Indiranagar", locality="Indiranagar",
                 lat=INDIRANAGAR[0], lng=INDIRANAGAR[1]))
    s.commit()
    yield s
    s.close()


def mk_provider(db, pid="PRV-001", **kw) -> Provider:
    role = kw.pop("role", "Nurse")
    defaults = dict(
        id=pid, name=f"Provider {pid} (demo)", role=role, active=True, verification_status="verified",
        credential_type="Demo credential", credential_expiry=date(2028, 1, 1),
        service_types=[k for k, v in SERVICES.items() if v["role"] == role], service_modes=["in_person"],
        capabilities=[], localities=["Indiranagar"], home_locality="Indiranagar", service_radius_km=10.0,
        base_lat=12.975, base_lng=77.64, work_days=[0, 1, 2, 3, 4, 5, 6], work_start=time(9, 0), work_end=time(18, 0),
        default_visit_minutes=45, buffer_minutes=15, max_daily_visits=5, max_daily_minutes=360,
        on_call_response_minutes=10, completed_visits=100, on_time_rate=0.90, cancellations=2, offers_received=120,
        offers_accepted=105, locality_visits={"Indiranagar": 10})
    defaults.update(kw)
    if role == "Concierge":
        defaults["credential_type"], defaults["credential_expiry"] = None, None
    p = Provider(**defaults)
    db.add(p)
    db.commit()
    return p


def mk_appt(db, provider, start: datetime, minutes=45, loc=KORAMANGALA, mode="in_person", locality="Koramangala"):
    a = Appointment(provider_id=provider.id, start_at=start, end_at=start + timedelta(minutes=minutes),
                    lat=loc[0] if mode == "in_person" else None, lng=loc[1] if mode == "in_person" else None,
                    locality=locality if mode == "in_person" else None, mode=mode)
    db.add(a)
    db.commit()
    return a


def mk_spec(service="vitals_check", mode="in_person", start=None, minutes=45, loc=INDIRANAGAR, **kw) -> VisitSpec:
    return VisitSpec(service=service, mode=mode, lat=loc[0], lng=loc[1], start_at=start or datetime.combine(DAY, time(10, 30)),
                     duration_minutes=minutes, parent_id="PAR-001", locality="Indiranagar", **kw)


class StubRouting:
    """Deterministic routing for tests. `fn(origin, dest) -> minutes | None`."""
    name, mode, label = "stub", "simulated", "Stub routing"

    def __init__(self, fn=None, fail: str | None = None):
        self.fn, self.fail, self.calls = fn or (lambda o, d: 20.0), fail, 0

    def matrix(self, origins, destinations, depart_at=None):
        self.calls += 1
        if self.fail:
            return MatrixResult([[None] * len(destinations) for _ in origins], "failed", self.fail)
        grid = []
        for o in origins:
            row = []
            for d in destinations:
                m = self.fn(o, d)
                km = max(haversine_km(*o, *d) * 1.3, 0.5)
                row.append(None if m is None else RouteResult(km, m, "simulated", False, "stub", "2026-10-30T03:30:00+00:00"))
            grid.append(row)
        got = sum(1 for r in grid for c in r if c)
        return MatrixResult(grid, "ok" if got == len(origins) * len(destinations) else ("failed" if got == 0 else "partial"))
