"""Central configuration for PeakAge Smart Assign.

Everything a product owner might want to tune lives here: scoring weights,
feasibility limits, the service catalog and the Bengaluru locality gazetteer.
The scoring weights are initial product hypotheses, not validated coefficients.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

# Bengaluru has no daylight saving. Appointment times are stored as naive
# wall-clock times in this zone and always exposed with an explicit +05:30 offset.
IST = ZoneInfo("Asia/Kolkata")
TIMEZONE_NAME = "Asia/Kolkata"


# --------------------------------------------------------------------------- #
# Scoring and feasibility policy
# --------------------------------------------------------------------------- #
DEFAULT_WEIGHTS = {
    "travel": 0.30,
    "continuity": 0.20,
    "reliability": 0.20,
    "schedule_fit": 0.15,
    "locality": 0.10,
    "workload": 0.05,
}


@dataclass(frozen=True)
class Policy:
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    # Travel score: 100 up to `travel_full_score_minutes`, then linear to 0 at
    # `max_travel_minutes`. ETAs are rounded to `travel_band_minutes` first so a
    # 1 minute difference in an uncertain estimate does not change the score.
    travel_full_score_minutes: int = 10
    max_travel_minutes: int = 60
    travel_band_minutes: int = 5

    # Reliability shrinkage: pull small samples towards a neutral prior.
    reliability_min_sample: int = 10
    prior_on_time_rate: float = 0.85
    prior_acceptance_rate: float = 0.80
    prior_cancellation_rate: float = 0.05
    prior_strength: int = 10
    reliability_mix: tuple = (0.50, 0.25, 0.25)  # on-time, acceptance, no-cancel

    # Continuity: 50 is neutral. Each prior completed visit adds, capped at 3.
    continuity_neutral: float = 50.0
    continuity_cap_visits: int = 3

    # Schedule fit: slack at or above this many minutes scores 100.
    slack_full_score_minutes: int = 30
    tight_slack_warning_minutes: int = 10

    # Locality fit
    locality_history_cap: int = 10

    # Hard limits
    max_on_call_response_minutes: int = 30
    on_call_gap_minutes: int = 5
    credential_expiry_warning_days: int = 30
    shortlist_size: int = 3

    # Geocoding guard: addresses outside this box are not Bengaluru.
    bengaluru_bbox: tuple = (12.70, 77.35, 13.25, 77.85)  # lat_min, lng_min, lat_max, lng_max


def load_policy() -> Policy:
    raw = os.getenv("SCORING_WEIGHTS")
    if not raw:
        return Policy()
    weights = json.loads(raw)
    if set(weights) != set(DEFAULT_WEIGHTS) or abs(sum(weights.values()) - 1.0) > 1e-6:
        raise ValueError("SCORING_WEIGHTS must name all six factors and sum to 1.0")
    return Policy(weights=weights)


POLICY = load_policy()


# --------------------------------------------------------------------------- #
# Service catalog
# --------------------------------------------------------------------------- #
ROLES = ["Nurse", "Concierge", "Doctor", "Dietitian", "Fitness Coach", "Counsellor"]

# Credentials the role must hold. Concierge has none; it needs verification only.
ROLE_CREDENTIAL = {
    "Nurse": "Nursing registration (demo)",
    "Doctor": "Medical registration (demo)",
    "Dietitian": "Dietetics certification (demo)",
    "Fitness Coach": "Fitness certification (demo)",
    "Counsellor": "Counselling qualification (demo)",
    "Concierge": None,
}

ROLE_CAPABILITIES = {
    "Nurse": ["wound_care", "diabetic_care", "geriatric_care", "injection_support"],
    "Concierge": ["hospital_escort", "pharmacy_runs", "kannada_speaking"],
    "Doctor": ["geriatrics", "internal_medicine", "kannada_speaking"],
    "Dietitian": ["diabetic_diet", "renal_diet", "kannada_speaking"],
    "Fitness Coach": ["balance_training", "chair_exercise", "kannada_speaking"],
    "Counsellor": ["grief_support", "family_counselling", "kannada_speaking"],
}

CAPABILITY_LABELS = {
    "wound_care": "Wound care",
    "diabetic_care": "Diabetic care",
    "geriatric_care": "Geriatric care",
    "injection_support": "Injection support",
    "hospital_escort": "Hospital escort",
    "pharmacy_runs": "Pharmacy runs",
    "kannada_speaking": "Speaks Kannada",
    "geriatrics": "Geriatrics",
    "internal_medicine": "Internal medicine",
    "diabetic_diet": "Diabetic diet",
    "renal_diet": "Renal diet",
    "balance_training": "Balance training",
    "chair_exercise": "Chair exercise",
    "grief_support": "Grief support",
    "family_counselling": "Family counselling",
}

# continuity=True means seeing the same person again matters for this service.
SERVICES = {
    "vitals_check": {"label": "Vitals and health check", "role": "Nurse", "modes": ["in_person"], "minutes": 45, "continuity": True},
    "wound_dressing": {"label": "Wound dressing", "role": "Nurse", "modes": ["in_person"], "minutes": 45, "continuity": True},
    "hospital_escort": {"label": "Hospital escort", "role": "Concierge", "modes": ["in_person"], "minutes": 180, "continuity": False},
    "errand_support": {"label": "Errand support", "role": "Concierge", "modes": ["in_person"], "minutes": 60, "continuity": False},
    "doctor_consult": {"label": "Doctor consultation", "role": "Doctor", "modes": ["in_person", "on_call"], "minutes": 30, "continuity": True},
    "diet_consult": {"label": "Diet consultation", "role": "Dietitian", "modes": ["in_person", "on_call"], "minutes": 40, "continuity": True},
    "fitness_session": {"label": "Fitness session", "role": "Fitness Coach", "modes": ["in_person", "on_call"], "minutes": 45, "continuity": True},
    "counselling_session": {"label": "Counselling session", "role": "Counsellor", "modes": ["in_person", "on_call"], "minutes": 50, "continuity": True},
}

MODE_LABELS = {"in_person": "In person", "on_call": "On call"}


# --------------------------------------------------------------------------- #
# Bengaluru localities (approximate centres, used for demo data and demo geocoding)
# --------------------------------------------------------------------------- #
LOCALITIES = {
    "Indiranagar": (12.9784, 77.6408),
    "Koramangala": (12.9352, 77.6245),
    "HSR Layout": (12.9116, 77.6389),
    "Whitefield": (12.9698, 77.7500),
    "Marathahalli": (12.9569, 77.7011),
    "Jayanagar": (12.9250, 77.5938),
    "JP Nagar": (12.9063, 77.5857),
    "BTM Layout": (12.9166, 77.6101),
    "Bellandur": (12.9304, 77.6784),
    "Malleshwaram": (13.0035, 77.5646),
    "Rajajinagar": (12.9916, 77.5520),
    "Hebbal": (13.0358, 77.5970),
}


# --------------------------------------------------------------------------- #
# Runtime settings from the environment
# --------------------------------------------------------------------------- #
class Settings:
    def __init__(self) -> None:
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./data/smart_assign.db")
        # Render's Postgres URLs may start with postgres://
        if self.database_url.startswith("postgres://"):
            self.database_url = self.database_url.replace("postgres://", "postgresql+psycopg://", 1)
        elif self.database_url.startswith("postgresql://"):
            self.database_url = self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        self.routing_mode = os.getenv("ROUTING_MODE", "demo").strip().lower()
        if self.routing_mode not in ("demo", "live"):
            raise ValueError("ROUTING_MODE must be 'demo' or 'live'")
        self.ola_api_key = os.getenv("OLA_MAPS_API_KEY", "").strip()
        self.ola_base_url = os.getenv("OLA_MAPS_BASE_URL", "https://api.olamaps.io").rstrip("/")
        self.ola_timeout = float(os.getenv("OLA_TIMEOUT_SECONDS", "8"))
        self.ola_retries = int(os.getenv("OLA_MAX_RETRIES", "2"))
        # Not verifiable from the public docs at build time, so deliberately conservative.
        self.ola_matrix_max_elements = int(os.getenv("OLA_MATRIX_MAX_ELEMENTS", "25"))
        self.frontend_origins = [o.strip() for o in os.getenv("FRONTEND_ORIGIN", "").split(",") if o.strip()]
        self.access_token = os.getenv("APP_ACCESS_TOKEN", "").strip()
        self.static_dir = os.getenv("STATIC_DIR", "")

    @property
    def live_configured(self) -> bool:
        return self.routing_mode == "live" and bool(self.ola_api_key)


def get_settings() -> Settings:
    return Settings()
