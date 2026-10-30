"""Routing abstraction.

RoutingProvider.matrix() returns road distance and duration for every origin x
destination pair. Implementations:

* DemoRoutingProvider   deterministic, simulated, never claims to be live
* OlaMapsRoutingProvider  Ola Maps Distance Matrix (needs OLA_MAPS_API_KEY)
* UnavailableRoutingProvider  live mode without a key: fails loudly, never falls back silently

A pair that cannot be routed is None. Straight-line distance is never returned as a
road distance.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

import httpx

from ..config import Settings
from ..geo import haversine_km

LatLng = tuple[float, float]


@dataclass(frozen=True)
class RouteResult:
    distance_km: float
    duration_min: float
    mode: str  # "live" | "simulated"
    traffic_aware: bool
    source: str  # "ola_maps" | "demo"
    computed_at: str

    def as_dict(self) -> dict:
        return {
            "distance_km": round(self.distance_km, 1),
            "duration_min": round(self.duration_min),
            "mode": self.mode,
            "traffic_aware": self.traffic_aware,
            "source": self.source,
            "computed_at": self.computed_at,
        }


@dataclass
class MatrixResult:
    grid: list[list[RouteResult | None]]
    status: str = "ok"  # ok | partial | failed
    error: str | None = None


class RoutingProvider(Protocol):
    name: str
    mode: str  # "simulated" | "live"
    label: str

    def matrix(self, origins: list[LatLng], destinations: list[LatLng], depart_at: datetime | None = None) -> MatrixResult: ...


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty(o: list, d: list) -> list[list[None]]:
    return [[None] * len(d) for _ in o]


# --------------------------------------------------------------------------- #
class DemoRoutingProvider:
    name = "demo"
    mode = "simulated"
    label = "Demo routing, not live traffic"

    CIRCUITY = 1.35  # road distance is longer than the crow flies

    def matrix(self, origins, destinations, depart_at=None) -> MatrixResult:
        stamp = _stamp()
        peak = depart_at is not None and (8 <= depart_at.hour < 11 or 17 <= depart_at.hour < 21)
        speed = 17.0 if peak else 24.0  # km/h, deterministic
        grid = []
        for o in origins:
            row = []
            for d in destinations:
                km = haversine_km(o[0], o[1], d[0], d[1]) * self.CIRCUITY
                minutes = km / speed * 60.0 + 3.0
                row.append(RouteResult(km, minutes, "simulated", False, "demo", stamp))
            grid.append(row)
        return MatrixResult(grid)


class UnavailableRoutingProvider:
    name = "ola_maps"
    mode = "live"
    label = "Live routing is not configured"

    def __init__(self, reason: str):
        self.reason = reason

    def matrix(self, origins, destinations, depart_at=None) -> MatrixResult:
        return MatrixResult(_empty(origins, destinations), "failed", self.reason)


# --------------------------------------------------------------------------- #
class OlaMapsRoutingProvider:
    """Ola Maps Distance Matrix: GET {base}/routing/v1/distanceMatrix.

    Verified against public sources: base URL, path, `origins`/`destinations` as
    "lat,lng|lat,lng", and `api_key` as the auth query parameter.
    NOT verifiable at build time: exact response field units, per-request element
    limits and traffic support. So:
      * numbers are read as metres and seconds, then sanity-checked against the
        straight-line distance; an element failing the check is dropped, not trusted
      * batch size is OLA_MATRIX_MAX_ELEMENTS (default 25)
      * results are labelled `traffic_aware=False` (standard)
    Run `python -m app.cli check-ola` to see the raw response for your key.
    """

    name = "ola_maps"
    mode = "live"
    label = "Live road routing (Ola Maps), standard ETA"

    def __init__(self, settings: Settings, client: httpx.Client | None = None, sleep=_time.sleep):
        self.s = settings
        self.client = client or httpx.Client(timeout=settings.ola_timeout)
        self._sleep = sleep

    # -- http ------------------------------------------------------------- #
    def _get(self, path: str, params: dict) -> tuple[dict | None, str | None]:
        params = {**params, "api_key": self.s.ola_api_key}
        last = "unknown error"
        for attempt in range(self.s.ola_retries + 1):
            try:
                r = self.client.get(f"{self.s.ola_base_url}{path}", params=params)
            except httpx.TimeoutException:
                last = "Ola Maps timed out"
            except httpx.HTTPError as e:
                last = f"Ola Maps request failed ({type(e).__name__})"
            else:
                if r.status_code in (401, 403):
                    return None, "Ola Maps rejected the API key (authentication failed)"
                if r.status_code == 429:
                    last = "Ola Maps rate limit reached"
                elif r.status_code >= 500:
                    last = f"Ola Maps server error ({r.status_code})"
                elif r.status_code >= 400:
                    return None, f"Ola Maps rejected the request ({r.status_code})"
                else:
                    try:
                        return r.json(), None
                    except ValueError:
                        return None, "Ola Maps returned a response that is not JSON"
            if attempt < self.s.ola_retries:
                self._sleep(0.5 * (2**attempt))
        return None, last

    # -- parsing ---------------------------------------------------------- #
    @staticmethod
    def _num(v):
        if isinstance(v, dict):
            v = v.get("value")
        return float(v) if isinstance(v, (int, float)) else None

    def _element(self, el: dict, o: LatLng, d: LatLng, stamp: str) -> RouteResult | None:
        if not isinstance(el, dict):
            return None
        status = el.get("status")
        if status is not None and str(status).upper() not in ("OK", "SUCCESS"):
            return None
        metres, seconds = self._num(el.get("distance")), self._num(el.get("duration"))
        if metres is None or seconds is None or metres < 0 or seconds < 0:
            return None
        km, minutes = metres / 1000.0, seconds / 60.0
        # Guard against a wrong unit assumption: a road route cannot be much shorter
        # than the straight line, nor absurdly fast.
        line = haversine_km(o[0], o[1], d[0], d[1])
        if km + 0.3 < line * 0.9:
            return None
        if minutes > 0 and km / (minutes / 60.0) > 130:
            return None
        return RouteResult(km, minutes, "live", False, "ola_maps", stamp)

    # -- public ----------------------------------------------------------- #
    def matrix(self, origins, destinations, depart_at=None) -> MatrixResult:
        if not origins or not destinations:
            return MatrixResult([])
        grid = _empty(origins, destinations)
        per_call = max(1, self.s.ola_matrix_max_elements // len(destinations))
        errors: list[str] = []
        stamp = _stamp()
        for start in range(0, len(origins), per_call):
            chunk = origins[start : start + per_call]
            data, err = self._get(
                "/routing/v1/distanceMatrix",
                {
                    "origins": "|".join(f"{a:.6f},{b:.6f}" for a, b in chunk),
                    "destinations": "|".join(f"{a:.6f},{b:.6f}" for a, b in destinations),
                },
            )
            if err or data is None:
                errors.append(err or "no data")
                if err and ("authentication" in err or "rate limit" in err):
                    break  # no point hammering
                continue
            rows = data.get("rows") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                errors.append("Ola Maps response did not contain route rows")
                continue
            for i, row in enumerate(rows[: len(chunk)]):
                els = row.get("elements") if isinstance(row, dict) else None
                for j, el in enumerate((els or [])[: len(destinations)]):
                    grid[start + i][j] = self._element(el, chunk[i], destinations[j], stamp)
        got = sum(1 for r in grid for c in r if c is not None)
        total = len(origins) * len(destinations)
        if got == total:
            return MatrixResult(grid)
        if got == 0:
            return MatrixResult(grid, "failed", errors[0] if errors else "No routes could be calculated")
        return MatrixResult(grid, "partial", errors[0] if errors else "Some routes could not be calculated")


def build_routing(settings: Settings) -> RoutingProvider:
    if settings.routing_mode == "demo":
        return DemoRoutingProvider()
    if not settings.ola_api_key:
        return UnavailableRoutingProvider("ROUTING_MODE is live but OLA_MAPS_API_KEY is not set")
    return OlaMapsRoutingProvider(settings)
