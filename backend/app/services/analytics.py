from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AssignmentAttempt, RecommendationRun
from .engine import EXCLUSIONS


def summary(db: Session) -> dict:
    runs = list(db.scalars(select(RecommendationRun)))
    attempts = list(db.scalars(select(AssignmentAttempt)))
    n = len(runs)
    ok = [a for a in attempts if a.success]
    lat = sorted(r.latency_ms for r in runs)
    excl: Counter = Counter()
    for r in runs:
        excl.update(r.exclusion_counts or {})
    overrides = [a for a in ok if a.chose_top is False]
    return {
        "recommendation_requests": n,
        "pct_with_feasible_provider": round(100 * sum(1 for r in runs if r.feasible_count > 0) / n, 1) if n else None,
        "avg_feasible_candidates": round(sum(r.feasible_count for r in runs) / n, 1) if n else None,
        "avg_providers_evaluated": round(sum(r.providers_evaluated for r in runs) / n, 1) if n else None,
        "assignment_attempts": len(attempts),
        "assignments_confirmed": len(ok),
        "top_ranked_selection_rate": round(100 * sum(1 for a in ok if a.chose_top) / len(ok), 1) if ok else None,
        "assignment_failure_rate": round(100 * (len(attempts) - len(ok)) / len(attempts), 1) if attempts else None,
        "latency_ms_avg": round(sum(lat) / n) if n else None,
        "latency_ms_p95": lat[min(n - 1, int(0.95 * n))] if n else None,
        "exclusion_reasons": [{"code": k, "reason": EXCLUSIONS.get(k, k), "count": v} for k, v in excl.most_common()],
        "routing_modes": dict(Counter((r.routing or {}).get("mode", "unknown") for r in runs)),
        "outcomes": dict(Counter(r.outcome for r in runs)),
        "lower_ranked_choices": [{"rank": a.selected_rank, "reason": a.override_reason or "No reason given"} for a in overrides][-20:],
        "failure_codes": dict(Counter(a.failure_code for a in attempts if not a.success)),
        "note": "Counts come from this prototype's own usage on synthetic data. There is no baseline, so none of these figures show that Smart Assign improves fulfilment or travel time.",
    }
