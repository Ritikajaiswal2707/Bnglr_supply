import { fireEvent, render, screen } from "@testing-library/react";
import CandidateCard from "./CandidateCard";
import type { Candidate } from "../lib/types";

const comp = (label: string, score: number, w: number, applicable = true) => ({ label, score, weight: w, effective_weight: applicable ? w : 0, applicable, note: `${label} note`, points: score * w });
const base: Candidate = {
  rank: 1, provider_id: "PRV-001", name: "Asha Rao (demo)", role: "Nurse", verification_status: "verified", home_locality: "Indiranagar", mode: "in_person",
  score: 82.4, route: { distance_km: 4.2, duration_min: 14, mode: "simulated", traffic_aware: false, source: "demo", computed_at: "" },
  components: { travel: comp("travel time", 90, 0.3), continuity: comp("continuity of care", 50, 0.2), reliability: comp("reliability", 88, 0.2), schedule_fit: comp("schedule fit", 100, 0.15), locality: comp("locality fit", 80, 0.1), workload: comp("workload balance", 70, 0.05) },
  origin: { kind: "home_base", label: "From base", lat: 12.97, lng: 77.64 }, travel_minutes: 14, distance_km: 4.2, straight_line_km: 3.1, straight_line_label: "Approximate straight-line distance",
  departs_at: null, earliest_arrival: "2026-11-02T10:16:00+05:30", slack_minutes: 40, availability: "Free at the requested time",
  workload: { visits_that_day: 1, visits_next_7_days: 4, max_daily_visits: 5 }, previous_visits_with_parent: 0,
  reliability: { completed_visits: 5, on_time_rate_adjusted: 0.86, evidence: "limited", cancellations: 0 },
  warnings: ["ETA is simulated, not live traffic"], reasons: ["14 min by road (4.2 km) from their base"], rank_note: "Highest overall score",
};

describe("CandidateCard", () => {
  it("shows rank, score, ETA and does not rely on colour alone", () => {
    render(<CandidateCard c={base} routingSimulated onSelect={() => undefined} />);
    expect(screen.getByText("Best match")).toBeInTheDocument();
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.getByText("Simulated ETA")).toBeInTheDocument();
    expect(screen.getByText("Limited history")).toBeInTheDocument();
    expect(screen.getByText(/Check:/)).toBeInTheDocument();
    expect(screen.getByText(/Approximate straight-line distance: 3.1 km/)).toBeInTheDocument();
  });
  it("lists all six score components with weights", () => {
    render(<CandidateCard c={base} routingSimulated onSelect={() => undefined} />);
    expect(screen.getByText("Travel time")).toBeInTheDocument();
    expect(screen.getByText("Workload balance")).toBeInTheDocument();
    expect(screen.getByText("30% weight")).toBeInTheDocument();
  });
  it("calls onSelect with the candidate", () => {
    const fn = vi.fn();
    render(<CandidateCard c={{ ...base, rank: 2 }} routingSimulated={false} onSelect={fn} />);
    expect(screen.getByText("Alternative 2")).toBeInTheDocument();
    expect(screen.getAllByText("Travel time")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Select provider" }));
    expect(fn).toHaveBeenCalledWith(expect.objectContaining({ provider_id: "PRV-001" }));
  });
  it("on-call candidates show no travel and mark travel as not used", () => {
    const c: Candidate = { ...base, mode: "on_call", route: null, travel_minutes: null, distance_km: null, straight_line_km: null, straight_line_label: null, earliest_arrival: null,
      components: { ...base.components, travel: comp("travel time", 100, 0.3, false) } };
    render(<CandidateCard c={c} routingSimulated onSelect={() => undefined} />);
    expect(screen.getByText("Remote session")).toBeInTheDocument();
    expect(screen.getByText("not used here")).toBeInTheDocument();
  });
});
