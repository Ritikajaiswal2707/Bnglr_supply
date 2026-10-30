"""Command line helpers.

  python -m app.cli init-db     create tables
  python -m app.cli seed        seed demo data if the database is empty
  python -m app.cli reset       drop everything and reseed the original demo data
  python -m app.cli check-ola   one live geocode and distance-matrix call, prints raw responses
"""
from __future__ import annotations

import json
import sys

from .config import get_settings
from .db import Base, get_engine, init_engine


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    s = get_settings()
    init_engine(s.database_url)
    if cmd == "init-db":
        Base.metadata.create_all(get_engine())
        print("Tables created.")
    elif cmd == "seed":
        from .seed import seed_if_empty

        print(seed_if_empty() or "Database already has data. Use `reset` to start over.")
    elif cmd == "reset":
        from .seed import reset_demo_data

        print(reset_demo_data())
    elif cmd == "check-ola":
        return check_ola(s)
    else:
        print(__doc__)
        return 2
    return 0


def check_ola(s) -> int:
    import httpx

    if not s.ola_api_key:
        print("OLA_MAPS_API_KEY is not set.")
        return 1
    base, key = s.ola_base_url, s.ola_api_key
    with httpx.Client(timeout=s.ola_timeout) as c:
        r = c.get(f"{base}/places/v1/geocode", params={"address": "Indiranagar, Bengaluru", "api_key": key})
        print("GEOCODE", r.status_code)
        print(json.dumps(r.json(), indent=2)[:1500] if r.headers.get("content-type", "").startswith("application/json") else r.text[:500])
        r = c.get(f"{base}/routing/v1/distanceMatrix", params={
            "origins": "12.9784,77.6408|12.9352,77.6245", "destinations": "12.9116,77.6389", "api_key": key})
        print("DISTANCE MATRIX", r.status_code)
        print(json.dumps(r.json(), indent=2)[:1500] if r.headers.get("content-type", "").startswith("application/json") else r.text[:500])
    print("\nShare these two raw responses if parsing needs adjusting. The key is not printed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
