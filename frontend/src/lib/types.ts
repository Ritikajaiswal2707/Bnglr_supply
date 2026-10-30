export type Mode = "in_person" | "on_call";

export interface Capability { key: string; label: string }
export interface ServiceDef { key: string; label: string; role: string; modes: Mode[]; minutes: number; capabilities: Capability[] }
export interface Locality { name: string; lat: number; lng: number }

export interface AppConfig {
  app: string;
  tagline: string;
  timezone: string;
  now: string;
  auth_required: boolean;
  routing: { configured_mode: string; live_configured: boolean; mode: "simulated" | "live"; label: string; traffic_aware: boolean };
  weights: Record<string, number>;
  policy: { max_travel_minutes: number; travel_band_minutes: number; shortlist_size: number; reliability_min_sample: number };
  services: ServiceDef[];
  mode_labels: Record<Mode, string>;
  roles: string[];
  localities: Locality[];
}

export interface Parent { id: string; name: string; address: string; locality: string; lat: number; lng: number }

export interface GeoCandidate { label: string; lat: number; lng: number; locality: string | null; source: string; approximate: boolean }
export interface GeocodeResponse { status: "ok" | "no_match" | "failed"; provider: string; candidates: GeoCandidate[]; error: string | null; routing_mode_label: string }

export interface Visit {
  id: number; parent_id: string; parent_name: string | null; service: string; service_label: string; role: string; mode: Mode;
  address: string; locality: string | null; lat: number; lng: number; geocode_source: string;
  start_at: string; end_at: string; duration_minutes: number; required_capabilities: string[];
  status: "unassigned" | "assigned" | "cancelled"; version: number;
  assignment: null | { id: number; provider_id: string; provider_name: string; status: string; assigned_by: string; assigned_at: string; score: number | null; rank: number | null };
}

export interface Component { label: string; score: number; weight: number; effective_weight: number; applicable: boolean; note: string; points: number }

export interface Candidate {
  rank: number; provider_id: string; name: string; role: string; verification_status: string; home_locality: string; mode: Mode;
  score: number; components: Record<string, Component>;
  route: { distance_km: number; duration_min: number; mode: "live" | "simulated"; traffic_aware: boolean; source: string; computed_at: string } | null;
  origin: { kind: string; label: string; lat: number | null; lng: number | null };
  travel_minutes: number | null; distance_km: number | null; straight_line_km: number | null; straight_line_label: string | null;
  departs_at: string | null; earliest_arrival: string | null; slack_minutes: number | null; availability: string;
  workload: { visits_that_day: number; visits_next_7_days: number; max_daily_visits: number };
  previous_visits_with_parent: number;
  reliability: { completed_visits: number; on_time_rate_adjusted: number; evidence: "limited" | "sufficient"; cancellations: number };
  warnings: string[]; reasons: string[]; rank_note: string;
}

export interface Exclusion { provider_id: string; name: string; role: string; code: string; reason: string; detail: string }

export interface Recommendation {
  recommendation_id: number; visit_id: number; outcome: "ok" | "no_feasible_provider" | "routing_failed"; message: string | null;
  candidates: Candidate[]; feasible_count: number; providers_evaluated: number; exclusion_counts: Record<string, number>;
  excluded: Exclusion[];
  routing: { provider: string; mode: "simulated" | "live" | string; label: string; traffic_aware: boolean; status: string; error: string | null; computed_at: string | null };
  latency_ms: number;
}

export interface ProviderRow {
  id: string; name: string; role: string; active: boolean; verification_status: string; home_locality: string; localities: string[];
  service_labels: string[]; service_modes: Mode[]; capabilities: string[]; credential_type: string | null; credential_expiry: string | null;
  credential_status: string; work_days: number[]; work_start: string; work_end: string; buffer_minutes: number; max_daily_visits: number;
  service_radius_km: number; on_call_response_minutes: number; completed_visits: number; on_time_rate: number; cancellations: number;
  upcoming_visits: number; base_approx: { lat: number; lng: number };
}
export interface ProviderDetail extends ProviderRow {
  appointments: { id: number; visit_id: number | null; start_at: string; end_at: string; locality: string | null; mode: Mode; service_label: string | null }[];
  leave_blocks: { id: number; start_at: string; end_at: string; reason: string }[];
}

export interface Analytics {
  recommendation_requests: number; pct_with_feasible_provider: number | null; avg_feasible_candidates: number | null;
  avg_providers_evaluated: number | null; assignment_attempts: number; assignments_confirmed: number;
  top_ranked_selection_rate: number | null; assignment_failure_rate: number | null; latency_ms_avg: number | null; latency_ms_p95: number | null;
  exclusion_reasons: { code: string; reason: string; count: number }[]; routing_modes: Record<string, number>; outcomes: Record<string, number>;
  lower_ranked_choices: { rank: number | null; reason: string }[]; failure_codes: Record<string, number>; note: string;
}
