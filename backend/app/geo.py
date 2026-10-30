from __future__ import annotations

import math

from .config import LOCALITIES, POLICY


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def in_bengaluru(lat: float, lng: float) -> bool:
    la0, ln0, la1, ln1 = POLICY.bengaluru_bbox
    return la0 <= lat <= la1 and ln0 <= lng <= ln1


def nearest_locality(lat: float, lng: float, max_km: float = 6.0) -> str | None:
    best, best_d = None, 1e9
    for name, (la, ln) in LOCALITIES.items():
        d = haversine_km(lat, lng, la, ln)
        if d < best_d:
            best, best_d = name, d
    return best if best_d <= max_km else None


def key(lat: float, lng: float) -> tuple[float, float]:
    return (round(lat, 5), round(lng, 5))
