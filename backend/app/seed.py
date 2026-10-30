"""Deterministic synthetic Bengaluru demo data.

Everything here is fictional. Names are generated from first/last name pools, there are
no real registration numbers, and credentials are labelled "(demo)". The random seed is
fixed, so the same structure is produced every time; dates are relative to `today`.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import LOCALITIES, ROLE_CAPABILITIES, ROLE_CREDENTIAL, SERVICES
from .db import Base, get_engine
from .geo import haversine_km
from .models import (Appointment, Assignment, LeaveBlock, Parent, ParentProviderHistory, Provider, Visit)
from .timeutil import now_ist

SEED = 20261030

FIRST = ["Anitha", "Ravi", "Meera", "Suresh", "Kavya", "Arjun", "Lakshmi", "Prakash", "Divya", "Naveen", "Shruti", "Karthik",
         "Pooja", "Mahesh", "Sneha", "Vikram", "Deepa", "Rohan", "Nisha", "Gopal", "Revathi", "Harish", "Swathi", "Manoj",
         "Bhavana", "Sandeep", "Rekha", "Vinay", "Asha", "Girish", "Padma", "Kiran", "Tanvi", "Rajesh", "Usha", "Faisal"]
LAST = ["Nair", "Gowda", "Iyer", "Reddy", "Hegde", "Kulkarni", "Rao", "Shetty", "Menon", "Bhat", "Patil", "Naidu", "Joshi",
        "Murthy", "Pillai", "Desai", "Acharya", "Kamath", "Prabhu", "Shenoy", "D'Souza", "Khan", "Sharma", "Varma"]
STREETS = ["1st Main", "5th Cross", "12th Main", "80 Feet Road", "Temple Street", "Lake View Road", "Park Avenue", "2nd Stage Road"]
PARENT_FIRST = ["Savitri", "Ramesh", "Kamala", "Venkat", "Girija", "Narayan", "Sarojini", "Balu", "Vimala", "Chandra", "Leela",
                "Seshadri", "Indira", "Mohan", "Padmini", "Krishna", "Radha", "Subbu", "Janaki", "Gopinath"]

ROLE_COUNTS = [("Nurse", 28), ("Concierge", 12), ("Doctor", 12), ("Dietitian", 12), ("Fitness Coach", 12), ("Counsellor", 14)]


def _nearest(loc: str, n: int) -> list[str]:
    la, ln = LOCALITIES[loc]
    others = sorted((o for o in LOCALITIES if o != loc), key=lambda o: haversine_km(la, ln, *LOCALITIES[o]))
    return others[:n]


def seed(db: Session, today: date | None = None) -> dict:
    rng = random.Random(SEED)
    today = today or now_ist().date()
    now = now_ist()
    locs = list(LOCALITIES)

    # ---- parents ----------------------------------------------------------
    parents: list[Parent] = []
    for i in range(1, 31):
        loc = locs[(i * 5 + rng.randint(0, 3)) % len(locs)]
        la, ln = LOCALITIES[loc]
        parents.append(Parent(
            id=f"PAR-{i:03d}", name=f"{rng.choice(PARENT_FIRST)} {rng.choice(LAST)} (demo)",
            address=f"{rng.randint(2, 98)}, {rng.choice(STREETS)}, {loc}, Bengaluru",
            locality=loc, lat=round(la + rng.uniform(-0.01, 0.01), 6), lng=round(ln + rng.uniform(-0.01, 0.01), 6)))
    db.add_all(parents)

    # ---- providers --------------------------------------------------------
    names = [f"{f} {l}" for f in FIRST for l in LAST]
    rng.shuffle(names)
    providers: list[Provider] = []
    n = 0
    for role, count in ROLE_COUNTS:
        role_services = [s for s, v in SERVICES.items() if v["role"] == role]
        for _ in range(count):
            n += 1
            home = locs[(n * 7 + rng.randint(0, 2)) % len(locs)]
            la, ln = LOCALITIES[home]
            served = [home] + _nearest(home, 3)[: rng.randint(1, 3)]
            modes_allowed = ["in_person"] if role in ("Nurse", "Concierge") else rng.choices(
                [["in_person", "on_call"], ["in_person"], ["on_call"]], weights=[60, 25, 15])[0]
            svc = role_services if rng.random() < 0.8 else [rng.choice(role_services)]
            cap_pool = ROLE_CAPABILITIES[role]
            caps = [c for c in cap_pool if rng.random() < 0.6] or [cap_pool[0]]
            novice = rng.random() < 0.15
            completed = rng.randint(0, 6) if novice else rng.randint(20, 450)
            offers = completed + rng.randint(5, 40)
            work_days = [0, 1, 2, 3, 4, 5] if rng.random() < 0.75 else [0, 1, 2, 3, 4]
            if rng.random() < 0.1:
                work_days = work_days + [6]
            ws = rng.choice([8, 9, 9, 10])
            we = rng.choice([17, 18, 18, 19, 20])
            r = rng.random()
            verification = "verified" if r < 0.88 else ("pending" if r < 0.96 else "rejected")
            cred = ROLE_CREDENTIAL[role]
            expiry = None
            if cred:
                x = rng.random()
                expiry = today - timedelta(days=rng.randint(5, 200)) if x < 0.06 else (
                    today + timedelta(days=rng.randint(3, 25)) if x < 0.14 else today + timedelta(days=rng.randint(60, 900)))
            lv = {loc: rng.randint(2, 30) for loc in served}
            providers.append(Provider(
                id=f"PRV-{n:03d}", name=names[n] + " (demo)", role=role, active=rng.random() > 0.04,
                verification_status=verification, credential_type=cred, credential_expiry=expiry,
                service_types=svc, service_modes=modes_allowed, capabilities=caps, localities=served, home_locality=home,
                service_radius_km=round(rng.uniform(7, 14), 1),
                base_lat=round(la + rng.uniform(-0.012, 0.012), 6), base_lng=round(ln + rng.uniform(-0.012, 0.012), 6),
                work_days=work_days, work_start=time(ws, 0), work_end=time(we, 0),
                default_visit_minutes=SERVICES[svc[0]]["minutes"], buffer_minutes=rng.choice([10, 15, 15, 20]),
                max_daily_visits=rng.choice([4, 5, 6]), max_daily_minutes=rng.choice([300, 360, 420]),
                on_call_response_minutes=rng.choice([5, 10, 15, 20, 45]),
                completed_visits=completed, on_time_rate=round(rng.uniform(0.72, 0.98), 3),
                cancellations=rng.randint(0, max(1, completed // 25)),
                offers_received=offers, offers_accepted=rng.randint(completed, offers),
                locality_visits=lv, is_demo=True))
    db.add_all(providers)
    db.flush()

    # ---- continuity history ----------------------------------------------
    for par in parents:
        picks = rng.sample([p for p in providers if par.locality in p.localities], k=min(3, sum(par.locality in p.localities for p in providers)))
        for p in picks:
            db.add(ParentProviderHistory(parent_id=par.id, provider_id=p.id, completed_visits=rng.randint(1, 6),
                                         last_visit_on=today - timedelta(days=rng.randint(3, 120))))

    # ---- leave ------------------------------------------------------------
    for p in rng.sample(providers, 10):
        d = today + timedelta(days=rng.randint(1, 5))
        full = rng.random() < 0.6
        s, e = (time(0, 0), time(23, 59)) if full else (time(12, 0), time(16, 0))
        db.add(LeaveBlock(provider_id=p.id, start_at=datetime.combine(d, s), end_at=datetime.combine(d, e),
                          reason="Planned leave (demo)" if full else "Training (demo)"))

    # ---- booked visits (each is a visit + assignment + calendar block) ------
    booked = 0
    for p in providers:
        if not p.active or p.verification_status != "verified":
            continue
        for off in range(0, 7):
            day = today + timedelta(days=off)
            if day.weekday() not in p.work_days or rng.random() > (0.55 if p.role == "Nurse" else 0.35):
                continue
            cursor = datetime.combine(day, p.work_start) + timedelta(minutes=rng.randint(0, 90))
            limit = datetime.combine(day, p.work_end) - timedelta(minutes=30)
            for _ in range(rng.choice([1, 1, 2, 3])):
                svc_key = rng.choice(p.service_types)
                dur = SERVICES[svc_key]["minutes"]
                end = cursor + timedelta(minutes=dur)
                if end > limit:
                    break
                near = [x for x in parents if x.locality in p.localities] or parents
                par = rng.choice(near)
                mode = "on_call" if ("on_call" in p.service_modes and SERVICES[svc_key]["modes"].count("on_call") and rng.random() < 0.25) else "in_person"
                if mode not in p.service_modes:
                    mode = p.service_modes[0]
                v = Visit(parent_id=par.id, service=svc_key, mode=mode, address=par.address, lat=par.lat, lng=par.lng,
                          locality=par.locality, geocode_source="demo", start_at=cursor, duration_minutes=dur,
                          required_capabilities=[], status="assigned")
                db.add(v)
                db.flush()
                db.add(Appointment(provider_id=p.id, visit_id=v.id, start_at=cursor, end_at=end,
                                   lat=par.lat if mode == "in_person" else None, lng=par.lng if mode == "in_person" else None,
                                   locality=par.locality if mode == "in_person" else None, mode=mode))
                db.add(Assignment(visit_id=v.id, provider_id=p.id, assigned_by="Demo seed", status="active",
                                  explanation="Seeded demo booking", assigned_at=now))
                booked += 1
                cursor = end + timedelta(minutes=rng.randint(75, 150))

    # ---- a few unassigned visits for the visit list ---------------------------
    unassigned = 0
    for i in range(6):
        par = parents[(i * 4) % len(parents)]
        svc_key = list(SERVICES)[(i * 3) % len(SERVICES)]
        day = today + timedelta(days=1 + i % 4)
        start = datetime.combine(day, time(rng.choice([10, 11, 14, 15, 16]), rng.choice([0, 30])))
        mode = SERVICES[svc_key]["modes"][0]
        db.add(Visit(parent_id=par.id, service=svc_key, mode=mode, address=par.address, lat=par.lat, lng=par.lng,
                     locality=par.locality, geocode_source="demo", start_at=start,
                     duration_minutes=SERVICES[svc_key]["minutes"], required_capabilities=[], status="unassigned"))
        unassigned += 1
    db.commit()
    return {"providers": len(providers), "parents": len(parents), "booked_visits": booked, "unassigned_visits": unassigned}


def reset_demo_data(today: date | None = None) -> dict:
    """Drop every table and rebuild the original demo dataset."""
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        return seed(db, today)


def seed_if_empty() -> dict | None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        if db.scalar(select(Provider.id).limit(1)) is None:
            return seed(db)
    return None
