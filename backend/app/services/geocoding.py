"""Geocoding abstraction. Never silently picks an address: callers get a list of
candidates (possibly empty) and the coordinator confirms one."""
from __future__ import annotations

import hashlib
import re
import time as _time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from ..config import LOCALITIES, Settings
from ..geo import in_bengaluru, nearest_locality


@dataclass
class GeoCandidate:
    label: str
    lat: float
    lng: float
    locality: str | None
    source: str  # "demo" | "ola"
    approximate: bool = False

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class GeocodeResult:
    candidates: list[GeoCandidate] = field(default_factory=list)
    status: str = "ok"  # ok | no_match | failed
    error: str | None = None
    provider: str = "demo"


class GeocodingProvider(Protocol):
    name: str

    def geocode(self, query: str) -> GeocodeResult: ...


_ALIASES = {"hsr": "HSR Layout", "btm": "BTM Layout", "jp nagar": "JP Nagar", "jayanagar": "Jayanagar"}
# Streets that exist in several localities, to exercise the "multiple matches" path.
_SHARED_STREETS = {
    "100 feet road": ["Indiranagar", "Koramangala"],
    "80 feet road": ["Koramangala", "HSR Layout"],
    "main road": ["Whitefield", "Marathahalli", "Hebbal"],
    "temple road": ["Malleshwaram", "Rajajinagar"],
}


class DemoGeocodingProvider:
    """Gazetteer lookup over the 12 demo localities. Coordinates are approximate and
    always flagged as such."""

    name = "demo"

    def _offset(self, text: str) -> tuple[float, float]:
        h = hashlib.sha256(text.encode()).digest()
        return ((h[0] / 255 - 0.5) * 0.012, (h[1] / 255 - 0.5) * 0.012)

    def geocode(self, query: str) -> GeocodeResult:
        q = re.sub(r"\s+", " ", query.strip().lower())
        if len(q) < 3:
            return GeocodeResult(status="no_match", error="Enter at least 3 characters of the address")
        found: list[str] = []
        for name in LOCALITIES:
            if name.lower() in q:
                found.append(name)
        for alias, name in _ALIASES.items():
            if re.search(rf"\b{re.escape(alias)}\b", q) and name not in found:
                found.append(name)
        for street, locs in _SHARED_STREETS.items():
            if street in q and not found:
                found.extend(locs)
        out = []
        for loc in found:
            lat, lng = LOCALITIES[loc]
            dlat, dlng = self._offset(q + loc)
            clean = re.sub(r"\s*,?\s*(bengaluru|bangalore)\s*$", "", query.strip(), flags=re.I)
            label = clean if loc.lower() in clean.lower() else f"{clean}, {loc}"
            out.append(GeoCandidate(f"{label}, Bengaluru", round(lat + dlat, 6), round(lng + dlng, 6), loc, "demo", True))
        if not out:
            return GeocodeResult(status="no_match", error="No match in the demo gazetteer. Include a Bengaluru locality such as Indiranagar or HSR Layout, or place the pin on the map.")
        return GeocodeResult(out)


class UnavailableGeocodingProvider:
    name = "ola_maps"

    def __init__(self, reason: str):
        self.reason = reason

    def geocode(self, query: str) -> GeocodeResult:
        return GeocodeResult(status="failed", error=self.reason, provider="ola_maps")


class OlaMapsGeocodingProvider:
    """GET {base}/places/v1/geocode?address=...&api_key=... (path and auth verified
    against public sources). The response is read defensively from
    `geocodingResults[].geometry.location` and `formatted_address`; if the shape is
    different the call is reported as a failure rather than guessed."""

    name = "ola_maps"

    def __init__(self, settings: Settings, client: httpx.Client | None = None, sleep=_time.sleep):
        self.s = settings
        self.client = client or httpx.Client(timeout=settings.ola_timeout)
        self._sleep = sleep

    def geocode(self, query: str) -> GeocodeResult:
        q = query.strip()
        if len(q) < 3:
            return GeocodeResult(status="no_match", error="Enter at least 3 characters of the address", provider="ola_maps")
        if not re.search(r"bengaluru|bangalore|bengalore", q, re.I):
            q = f"{q}, Bengaluru"
        params = {"address": q, "api_key": self.s.ola_api_key}
        last = "Geocoding failed"
        for attempt in range(self.s.ola_retries + 1):
            try:
                r = self.client.get(f"{self.s.ola_base_url}/places/v1/geocode", params=params)
            except httpx.TimeoutException:
                last = "Ola Maps timed out while looking up the address"
            except httpx.HTTPError:
                last = "Ola Maps could not be reached"
            else:
                if r.status_code in (401, 403):
                    return GeocodeResult(status="failed", error="Ola Maps rejected the API key (authentication failed)", provider="ola_maps")
                if r.status_code == 429 or r.status_code >= 500:
                    last = "Ola Maps rate limit reached" if r.status_code == 429 else f"Ola Maps server error ({r.status_code})"
                elif r.status_code >= 400:
                    return GeocodeResult(status="failed", error=f"Ola Maps rejected the request ({r.status_code})", provider="ola_maps")
                else:
                    return self._parse(r)
            if attempt < self.s.ola_retries:
                self._sleep(0.5 * (2**attempt))
        return GeocodeResult(status="failed", error=last, provider="ola_maps")

    def _parse(self, r: httpx.Response) -> GeocodeResult:
        try:
            data = r.json()
        except ValueError:
            return GeocodeResult(status="failed", error="Ola Maps returned a response that is not JSON", provider="ola_maps")
        results = data.get("geocodingResults") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return GeocodeResult(status="failed", error="Unexpected Ola Maps geocoding response", provider="ola_maps")
        out, outside = [], 0
        for item in results:
            try:
                loc = item["geometry"]["location"]
                lat, lng = float(loc["lat"]), float(loc["lng"])
                label = str(item.get("formatted_address") or item.get("name") or "").strip()
            except (KeyError, TypeError, ValueError):
                continue
            if not label:
                continue
            if not in_bengaluru(lat, lng):
                outside += 1
                continue
            out.append(GeoCandidate(label, round(lat, 6), round(lng, 6), nearest_locality(lat, lng), "ola", False))
        if out:
            return GeocodeResult(out, provider="ola_maps")
        msg = "Address resolved outside Bengaluru. This prototype covers Bengaluru only." if outside else "No match found for this address"
        return GeocodeResult(status="no_match", error=msg, provider="ola_maps")


def build_geocoder(settings: Settings) -> GeocodingProvider:
    if settings.routing_mode == "demo":
        return DemoGeocodingProvider()
    if not settings.ola_api_key:
        return UnavailableGeocodingProvider("ROUTING_MODE is live but OLA_MAPS_API_KEY is not set")
    return OlaMapsGeocodingProvider(settings)
