import { useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { Analytics, AppConfig } from "../lib/types";
import { Note } from "./ui";

const n = (v: number | null, s = "") => (v === null ? "No data" : `${v}${s}`);

export default function Insights({ config }: { config: AppConfig }) {
  const [a, setA] = useState<Analytics | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => api<Analytics>("/api/analytics/summary").then(setA).catch((e: ApiError) => setErr(e.message));
  useEffect(() => { load(); }, []);

  async function reset() {
    if (!window.confirm("Reset all demo data? Visits, assignments and analytics will be replaced with the original dataset.")) return;
    try { const r = await api<{ providers: number }>("/api/admin/reset-demo", { method: "POST" }); setMsg(`Demo data restored: ${r.providers} providers.`); load(); } catch (e) { setErr((e as ApiError).message); }
  }
  if (err) return <Note tone="bad" title="Could not load insights.">{err}</Note>;
  if (!a) return <div className="skel" />;
  const max = Math.max(1, ...a.exclusion_reasons.map((x) => x.count));
  return (
    <div className="stack">
      <section className="card">
        <h2>Evaluation</h2>
        <p className="cap" style={{ marginTop: 4 }}>{a.note}</p>
      </section>
      <div className="stat-grid">
        <div className="stat tile-peach"><div className="big">{a.recommendation_requests}</div><div className="lbl">Recommendation requests</div></div>
        <div className="stat tile-green"><div className="big">{n(a.pct_with_feasible_provider)}<small>{a.pct_with_feasible_provider === null ? "" : "%"}</small></div><div className="lbl">Requests with at least one feasible provider</div></div>
        <div className="stat tile-blue"><div className="big">{n(a.avg_feasible_candidates)}</div><div className="lbl">Average feasible candidates</div></div>
        <div className="stat tile-yellow"><div className="big">{n(a.top_ranked_selection_rate)}<small>{a.top_ranked_selection_rate === null ? "" : "%"}</small></div><div className="lbl">Top-ranked chosen ({a.assignments_confirmed} confirmed)</div></div>
        <div className="stat tile-lilac"><div className="big">{n(a.assignment_failure_rate)}<small>{a.assignment_failure_rate === null ? "" : "%"}</small></div><div className="lbl">Assignment failure rate ({a.assignment_attempts} attempts)</div></div>
        <div className="stat tile-blue"><div className="big">{n(a.latency_ms_avg)}<small>{a.latency_ms_avg === null ? "" : "ms"}</small></div><div className="lbl">Average latency{a.latency_ms_p95 !== null ? `, p95 ${a.latency_ms_p95} ms` : ""}</div></div>
      </div>
      <section className="card">
        <h3 style={{ marginBottom: 12 }}>Why providers were excluded</h3>
        {a.exclusion_reasons.length === 0 ? <p className="cap">Run a few recommendations to see this.</p> : (
          <div className="barlist">{a.exclusion_reasons.map((x) => (
            <div className="bar-row" key={x.code}><div className="bar-top"><span>{x.reason}</span><b>{x.count}</b></div><div className="track"><div className="fill ink" style={{ width: `${(x.count / max) * 100}%` }} /></div></div>
          ))}</div>
        )}
        <p className="cap" style={{ marginTop: 10 }}>Role mismatch counts every provider with a different role, so it is usually the largest bar.</p>
      </section>
      <section className="card">
        <h3 style={{ marginBottom: 8 }}>Routing used</h3>
        <p>{Object.entries(a.routing_modes).map(([k, v]) => `${v} ${k === "simulated" ? "simulated" : k === "live" ? "live" : k}`).join(", ") || "No requests yet"}</p>
        {a.lower_ranked_choices.length > 0 && <><h3 style={{ margin: "16px 0 8px" }}>Lower-ranked choices</h3><ul className="reasons">{a.lower_ranked_choices.map((c, i) => <li key={i}>Rank {c.rank}: {c.reason}</li>)}</ul></>}
      </section>
      <section className="card">
        <h3 style={{ marginBottom: 8 }}>Scoring weights</h3>
        <table className="t"><thead><tr><th>Factor</th><th className="num">Weight</th></tr></thead><tbody>{Object.entries(config.weights).map(([k, w]) => <tr key={k}><td>{k.replace("_", " ")}</td><td className="num">{Math.round(w * 100)}%</td></tr>)}</tbody></table>
        <p className="cap" style={{ marginTop: 8 }}>These are starting hypotheses, not validated numbers. They live in one place in the backend configuration.</p>
      </section>
      <section className="card">
        <h3 style={{ marginBottom: 8 }}>Demo data</h3>
        <p className="cap" style={{ marginBottom: 10 }}>Everything here is synthetic. Resetting restores the original seeded dataset.</p>
        <button className="btn ghost" onClick={reset}>Reset demo data</button>
        {msg && <p className="cap" style={{ marginTop: 8 }}>{msg}</p>}
      </section>
    </div>
  );
}
