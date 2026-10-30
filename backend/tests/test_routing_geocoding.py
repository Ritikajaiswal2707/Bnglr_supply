"""Ola Maps adapters against a mocked HTTP transport, demo providers, timezone handling."""
from datetime import datetime, timezone

import httpx

from app.config import IST, Settings
from app.services.geocoding import DemoGeocodingProvider, OlaMapsGeocodingProvider, build_geocoder
from app.services.routing import DemoRoutingProvider, OlaMapsRoutingProvider, UnavailableRoutingProvider, build_routing
from app.timeutil import iso, to_ist_naive

A, B, C = (12.9784, 77.6408), (12.9352, 77.6245), (12.9116, 77.6389)
KEY = "SECRET-KEY-123"


def settings(**env) -> Settings:
    s = Settings()
    s.ola_api_key, s.ola_retries = KEY, 2
    for k, v in env.items():
        setattr(s, k, v)
    return s


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def matrix_json(rows):
    return {"status": "SUCCESS", "rows": [{"elements": e} for e in rows]}


OK = lambda m, s: {"status": "OK", "distance": m, "duration": s}


# ---- 13, 14: geocoding ------------------------------------------------------------
def test_demo_geocoder_returns_multiple_matches_for_ambiguous_street():
    res = DemoGeocodingProvider().geocode("12, 100 Feet Road")
    assert len(res.candidates) == 2 and all(c.approximate and c.source == "demo" for c in res.candidates)
    assert {c.locality for c in res.candidates} == {"Indiranagar", "Koramangala"}


def test_demo_geocoder_unrecognised_address():
    res = DemoGeocodingProvider().geocode("Some place nobody knows")
    assert res.status == "no_match" and res.candidates == [] and "pin" in res.error


def test_ola_geocoder_multiple_matches_and_outside_city_filtered():
    body = {"geocodingResults": [
        {"formatted_address": "A Road, Indiranagar, Bengaluru", "geometry": {"location": {"lat": 12.97, "lng": 77.64}}},
        {"formatted_address": "A Road, Koramangala, Bengaluru", "geometry": {"location": {"lat": 12.93, "lng": 77.62}}},
        {"formatted_address": "A Road, Mumbai", "geometry": {"location": {"lat": 19.07, "lng": 72.87}}}]}
    g = OlaMapsGeocodingProvider(settings(), client(lambda r: httpx.Response(200, json=body)))
    res = g.geocode("A Road")
    assert res.status == "ok" and len(res.candidates) == 2 and all(c.source == "ola" for c in res.candidates)


def test_ola_geocoder_sends_key_and_bengaluru_hint():
    seen = {}
    def h(r):
        seen.update(dict(r.url.params))
        return httpx.Response(200, json={"geocodingResults": []})
    OlaMapsGeocodingProvider(settings(), client(h)).geocode("5th Cross")
    assert seen["api_key"] == KEY and "Bengaluru" in seen["address"]


def test_ola_geocoder_failures_do_not_leak_key_or_guess():
    for resp in (httpx.Response(500), httpx.Response(200, json={"unexpected": 1}), httpx.Response(200, text="<html>")):
        g = OlaMapsGeocodingProvider(settings(), client(lambda r, resp=resp: resp), sleep=lambda s: None)
        res = g.geocode("Indiranagar")
        assert res.status == "failed" and res.candidates == [] and KEY not in (res.error or "")


def test_ola_geocoder_only_outside_bengaluru():
    body = {"geocodingResults": [{"formatted_address": "X", "geometry": {"location": {"lat": 28.6, "lng": 77.2}}}]}
    res = OlaMapsGeocodingProvider(settings(), client(lambda r: httpx.Response(200, json=body))).geocode("X Road")
    assert res.status == "no_match" and "outside Bengaluru" in res.error


# ---- 15, 16, 18: routing failures --------------------------------------------------
def test_routing_timeout_retries_then_fails_cleanly():
    calls = []
    def h(r):
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=r)
    m = OlaMapsRoutingProvider(settings(), client(h), sleep=lambda s: None).matrix([A, B], [C])
    assert len(calls) == 3 and m.status == "failed" and "timed out" in m.error
    assert all(c is None for row in m.grid for c in row)


def test_partial_matrix_marks_missing_elements_none():
    body = matrix_json([[OK(9000, 900)], [{"status": "NOT_FOUND"}]])
    m = OlaMapsRoutingProvider(settings(), client(lambda r: httpx.Response(200, json=body))).matrix([A, B], [C])
    assert m.status == "partial" and m.grid[0][0].duration_min == 15 and m.grid[1][0] is None
    assert m.grid[0][0].mode == "live" and m.grid[0][0].traffic_aware is False


def test_auth_error_is_reported_without_retry_storm():
    calls = []
    def h(r):
        calls.append(1)
        return httpx.Response(401)
    m = OlaMapsRoutingProvider(settings(), client(h), sleep=lambda s: None).matrix([A], [C])
    assert m.status == "failed" and "authentication" in m.error and len(calls) == 1 and KEY not in m.error


def test_rate_limit_retries_then_reports():
    calls = []
    def h(r):
        calls.append(1)
        return httpx.Response(429)
    m = OlaMapsRoutingProvider(settings(), client(h), sleep=lambda s: None).matrix([A], [C])
    assert m.status == "failed" and "rate limit" in m.error and len(calls) == 3


def test_requests_are_batched_by_element_limit():
    sizes = []
    def h(r):
        n = len(r.url.params["origins"].split("|"))
        sizes.append(n)
        return httpx.Response(200, json=matrix_json([[OK(30000, 2400)]] * n))
    origins = [(12.90 + i * 0.001, 77.60) for i in range(60)]
    m = OlaMapsRoutingProvider(settings(ola_matrix_max_elements=25), client(h)).matrix(origins, [(12.99, 77.60)])
    assert sizes == [25, 25, 10] and m.status == "ok"


def test_implausible_units_are_rejected_not_trusted():
    # 6 "km" reported as 6 metres: road distance cannot be shorter than the straight line
    body = matrix_json([[OK(6, 900)]])
    m = OlaMapsRoutingProvider(settings(), client(lambda r: httpx.Response(200, json=body))).matrix([A], [C])
    assert m.status == "failed" and m.grid[0][0] is None


def test_numeric_value_objects_are_also_understood():
    body = matrix_json([[{"status": "OK", "distance": {"value": 9000}, "duration": {"value": 900}}]])
    m = OlaMapsRoutingProvider(settings(), client(lambda r: httpx.Response(200, json=body))).matrix([A], [C])
    assert m.grid[0][0].distance_km == 9.0


# ---- 17: missing credentials / demo mode ----------------------------------------------
def test_live_mode_without_key_fails_loudly_never_falls_back_to_demo():
    s = Settings(); s.routing_mode, s.ola_api_key = "live", ""
    r = build_routing(s)
    assert isinstance(r, UnavailableRoutingProvider)
    m = r.matrix([A], [C])
    assert m.status == "failed" and "OLA_MAPS_API_KEY" in m.error
    g = build_geocoder(s).geocode("Indiranagar")
    assert g.status == "failed" and "OLA_MAPS_API_KEY" in g.error


def test_demo_routing_is_deterministic_labelled_simulated():
    r = DemoRoutingProvider()
    m1, m2 = r.matrix([A, B], [C]), r.matrix([A, B], [C])
    assert [x.duration_min for row in m1.grid for x in row] == [x.duration_min for row in m2.grid for x in row]
    c = m1.grid[0][0]
    assert c.mode == "simulated" and c.source == "demo" and c.traffic_aware is False
    assert r.label == "Demo routing, not live traffic"
    assert r.matrix([A], [C], datetime(2026, 11, 2, 9, 0)).grid[0][0].duration_min > r.matrix([A], [C], datetime(2026, 11, 2, 14, 0)).grid[0][0].duration_min


# ---- 24: timezones ----------------------------------------------------------------------
def test_ist_wall_times_are_stable_and_exported_with_offset():
    local = datetime(2026, 11, 2, 10, 30)
    assert iso(local) == "2026-11-02T10:30:00+05:30"
    assert to_ist_naive(datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)) == local
    assert to_ist_naive(local.replace(tzinfo=IST)) == local
    # Dates where Europe and the US change clocks must not shift Bengaluru times.
    for d in [(2026, 3, 8), (2026, 3, 29), (2026, 10, 25), (2026, 11, 1)]:
        t = datetime(*d, 10, 30)
        assert iso(t).endswith("T10:30:00+05:30")
        assert to_ist_naive(datetime(*d, 5, 0, tzinfo=timezone.utc)) == datetime(*d, 10, 30)
