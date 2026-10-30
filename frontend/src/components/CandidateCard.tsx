import type { Candidate } from "../lib/types";
import { pct, timeOf, initials } from "../lib/format";
import { Pill, verificationPill } from "./ui";

export function ScoreBars({ c }: { c: Candidate }) {
  return (
    <div className="bars">
      {Object.entries(c.components).map(([k, comp]) => (
        <div className="bar-row" key={k}>
          <div className="bar-top">
            <span><b>{comp.label[0].toUpperCase() + comp.label.slice(1)}</b> <span className="cap">{comp.applicable ? `${Math.round(comp.effective_weight * 100)}% weight` : "not used here"}</span></span>
            <span>{comp.applicable ? `${Math.round(comp.score)}/100` : "n/a"}</span>
          </div>
          <div className="track" aria-hidden="true"><div className="fill" style={{ width: `${comp.applicable ? comp.score : 0}%` }} /></div>
          <div className="cap">{comp.note}</div>
        </div>
      ))}
    </div>
  );
}

interface Props { c: Candidate; routingSimulated: boolean; onSelect: (c: Candidate) => void }

export default function CandidateCard({ c, routingSimulated, onSelect }: Props) {
  const best = c.rank === 1;
  const onCall = c.mode === "on_call";
  const body = (
    <>
      <div className="cand-head">
        <div className="avatar" style={best ? { background: "#F9C6B2" } : undefined} aria-hidden="true">{initials(c.name)}</div>
        <div className="grow" style={{ flex: 1, minWidth: 0 }}>
          <div className="rank-line">
            <Pill tone={best ? "peach" : "info"}>{best ? "Best match" : `Alternative ${c.rank}`}</Pill>
            {best ? <Pill tone="dark">{verificationPillText(c.verification_status)}</Pill> : verificationPill(c.verification_status)}
          </div>
          <h3 style={{ marginTop: 6 }}>{c.name}</h3>
          <div className="cap">{c.role}, based in {c.home_locality}</div>
        </div>
      </div>

      <div className="metrics">
        <div className="metric"><div className="v">{Math.round(c.score)}<small>/100</small></div><div className="k">Match score</div></div>
        <div className="metric"><div className="v">{onCall ? "None" : <>{c.travel_minutes}<small>min</small></>}</div><div className="k">{onCall ? "Remote session" : routingSimulated ? "Simulated ETA" : "Travel time"}</div></div>
        <div className="metric"><div className="v">{onCall ? "n/a" : <>{c.distance_km}<small>km</small></>}</div><div className="k">{onCall ? "No road distance" : "Road distance"}</div></div>
      </div>

      <div className="rank-line">
        <Pill tone={best ? "dark" : "good"}>Available</Pill>
        <span className="cap">{c.availability}{c.earliest_arrival && ` · arrives about ${timeOf(c.earliest_arrival)}`}</span>
      </div>

      <ul className="reasons">{c.reasons.map((r) => <li key={r}>{r}</li>)}</ul>

      <div className="rank-line">
        <Pill tone={best ? "dark" : c.reliability.evidence === "limited" ? "warn" : "info"}>
          {c.reliability.evidence === "limited" ? "Limited history" : `${pct(c.reliability.on_time_rate_adjusted)} on time`}
        </Pill>
        <Pill tone={best ? "dark" : ""}>{c.workload.visits_that_day} of {c.workload.max_daily_visits} visits that day</Pill>
        {c.previous_visits_with_parent > 0 && <Pill tone={best ? "dark" : "info"}>Seen this parent {c.previous_visits_with_parent}x</Pill>}
      </div>

      {c.warnings.map((w) => <div className="note warn" key={w}><div><b>Check:</b> {w}</div></div>)}
      {c.rank_note && !best && <p className="cap">{c.rank_note}</p>}

      <details>
        <summary>How this score was calculated</summary>
        <ScoreBars c={c} />
        {c.straight_line_km != null && <p className="cap" style={{ marginTop: 10 }}>{c.straight_line_label}: {c.straight_line_km} km. This is not a road distance.</p>}
      </details>

      <button className={`btn block ${best ? "primary" : ""}`} onClick={() => onSelect(c)}>Select provider</button>
    </>
  );
  return best ? <article className="feature cand" aria-label={`Best match: ${c.name}`}>{body}</article> : <article className="card cand" aria-label={`Alternative ${c.rank}: ${c.name}`}>{body}</article>;
}

const verificationPillText = (s: string) => (s === "verified" ? "Verified" : s === "pending" ? "Verification pending" : "Verification rejected");
