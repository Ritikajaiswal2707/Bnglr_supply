import { useState } from "react";
import { api, ApiError } from "../lib/api";
import { dateTimeOf, timeOf } from "../lib/format";
import type { Candidate, Recommendation, Visit } from "../lib/types";
import { KV, Note, Pill, Sheet } from "./ui";
import { ScoreBars } from "./CandidateCard";

interface Props {
  visit: Visit;
  rec: Recommendation;
  cand: Candidate;
  onClose: () => void;
  onDone: (r: { provider: string; score: number; notification: string }) => void;
  onRerun: () => void;
}

const RERUN = new Set(["SCHEDULE_CHANGED", "RECOMMENDATION_STALE", "APPOINTMENT_CONFLICT", "ON_LEAVE", "INSUFFICIENT_TRAVEL_BUFFER", "WORKLOAD_LIMIT", "OUTSIDE_WORKING_HOURS", "WORKING_HOURS_EXCEEDED", "ROUTE_UNAVAILABLE", "INACTIVE", "VERIFICATION_INCOMPLETE", "CREDENTIAL_EXPIRED", "TRAVEL_TOO_LONG"]);

export default function ConfirmSheet({ visit, rec, cand, onClose, onDone, onRerun }: Props) {
  const [by, setBy] = useState("City Coordinator");
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; code?: string } | null>(null);

  async function confirm() {
    setBusy(true);
    setErr(null);
    try {
      const r = await api<{ score: number; notification: string }>(`/api/visits/${visit.id}/assign`, {
        method: "POST",
        body: { provider_id: cand.provider_id, recommendation_id: rec.recommendation_id, assigned_by: by.trim() || "City Coordinator", override_reason: why.trim() || null },
      });
      onDone({ provider: cand.name, score: r.score, notification: r.notification });
    } catch (e) {
      const a = e as ApiError;
      setErr({ message: a.message, code: a.code });
    } finally {
      setBusy(false);
    }
  }

  const onCall = cand.mode === "on_call";
  return (
    <Sheet label="Confirm assignment" onClose={onClose}>
      <h2 style={{ marginBottom: 4 }}>Confirm assignment</h2>
      <p className="cap" style={{ marginBottom: 12 }}>Nothing is saved until you confirm. Availability is checked again at that moment.</p>
      <div className="stack">
        <div className="card flat">
          <KV rows={[
            ["Parent", visit.parent_name ?? visit.parent_id],
            ["Service", `${visit.service_label}, ${onCall ? "on call" : "in person"}`],
            ["When", `${dateTimeOf(visit.start_at)}, ${visit.duration_minutes} min`],
            ["Provider", <>{cand.name} <Pill tone={cand.rank === 1 ? "peach" : "info"}>{cand.rank === 1 ? "Best match" : `Alternative ${cand.rank}`}</Pill></>],
            ["Travel", onCall ? "Remote, no travel" : `${cand.travel_minutes} min, ${cand.distance_km} km (${cand.route?.mode === "live" ? "live" : "simulated"})`],
            ["Arrives about", onCall ? "Not applicable" : timeOf(cand.earliest_arrival)],
            ["Match score", `${Math.round(cand.score)} out of 100`],
          ]} />
        </div>
        {cand.warnings.map((w) => <Note tone="warn" title="Check:" key={w}>{w}</Note>)}
        {cand.route?.mode === "simulated" && <Note tone="info">Travel figures are simulated. They are not live traffic.</Note>}
        <details className="card flat">
          <summary>Why this provider scored {Math.round(cand.score)}</summary>
          <ScoreBars c={cand} />
          {cand.rank_note && <p className="cap" style={{ marginTop: 10 }}>{cand.rank_note}</p>}
        </details>

        <div className="form">
          <div className="field"><label htmlFor="by">Assigned by</label><input id="by" className="input" value={by} maxLength={80} onChange={(e) => setBy(e.target.value)} /></div>
          {cand.rank > 1 && (
            <div className="field">
              <label htmlFor="why">Why not the best match? (optional)</label>
              <input id="why" className="input" value={why} maxLength={255} onChange={(e) => setWhy(e.target.value)} placeholder="For example, the parent prefers this provider" />
            </div>
          )}
        </div>

        {err && (
          <Note tone="bad" title="Could not assign.">
            {err.message}
            {err.code && RERUN.has(err.code) && <div style={{ marginTop: 8 }}><button className="btn sm" onClick={onRerun}>Run Find providers again</button></div>}
          </Note>
        )}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button className="btn ghost" onClick={onClose}>Back</button>
          <button className="btn" onClick={confirm} disabled={busy}>{busy ? "Assigning" : "Confirm assignment"}</button>
        </div>
        <p className="cap">The provider notification is simulated. No SMS or WhatsApp message is sent.</p>
      </div>
    </Sheet>
  );
}
