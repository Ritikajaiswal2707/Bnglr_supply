import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import { dateTimeOf, dayNames, initials, pct, roleTile, timeOf, workDays } from "../lib/format";
import type { AppConfig, ProviderDetail, ProviderRow, Visit } from "../lib/types";
import { KV, Note, Pill, Sheet, verificationPill } from "./ui";

const credTone = (s: string) => (s === "valid" || s === "not_required" ? "good" : s === "expiring_soon" ? "warn" : "bad");
const credText: Record<string, string> = { valid: "Credential valid", not_required: "No credential needed", expiring_soon: "Credential expiring soon", expired: "Credential expired", missing: "Credential missing" };

export default function Providers({ config }: { config: AppConfig }) {
  const [q, setQ] = useState(""); const [role, setRole] = useState(""); const [ver, setVer] = useState(""); const [loc, setLoc] = useState("");
  const [rows, setRows] = useState<ProviderRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(() => {
    const p = new URLSearchParams();
    if (q.trim()) p.set("q", q.trim()); if (role) p.set("role", role); if (ver) p.set("verification", ver); if (loc) p.set("locality", loc);
    api<ProviderRow[]>(`/api/providers?${p}`).then((r) => { setRows(r); setError(null); }).catch((e: ApiError) => setError(e.message));
  }, [q, role, ver, loc]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);

  return (
    <div className="stack">
      <section className="card">
        <h2 style={{ marginBottom: 12 }}>Provider directory</h2>
        <div className="filters">
          <div className="field"><label htmlFor="pq">Search name or ID</label><input id="pq" className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Anitha or PRV-012" /></div>
          <div className="field"><label htmlFor="pr">Role</label><select id="pr" className="input" value={role} onChange={(e) => setRole(e.target.value)}><option value="">All roles</option>{config.roles.map((r) => <option key={r}>{r}</option>)}</select></div>
          <div className="field"><label htmlFor="pv">Verification</label><select id="pv" className="input" value={ver} onChange={(e) => setVer(e.target.value)}><option value="">Any</option><option value="verified">Verified</option><option value="pending">Pending</option><option value="rejected">Rejected</option></select></div>
          <div className="field"><label htmlFor="pl">Locality</label><select id="pl" className="input" value={loc} onChange={(e) => setLoc(e.target.value)}><option value="">All localities</option>{config.localities.map((l) => <option key={l.name}>{l.name}</option>)}</select></div>
        </div>
      </section>
      {error && <Note tone="bad" title="Could not load providers.">{error}</Note>}
      {rows === null ? <div className="skel" /> : rows.length === 0 ? <div className="card empty">No providers match these filters.</div> : (
        <>
          <p className="cap" style={{ marginLeft: 4 }}>{rows.length} provider{rows.length === 1 ? "" : "s"}. All records are synthetic demo data.</p>
          <div className="list">
            {rows.map((p) => (
              <button key={p.id} className="item" onClick={() => setOpen(p.id)}>
                <div className={`avatar ${roleTile[p.role] ?? ""}`} aria-hidden="true">{initials(p.name)}</div>
                <div className="grow"><div className="t">{p.name}</div><div className="s">{p.id} · {p.role} · {p.home_locality} · {p.upcoming_visits} visits this week</div></div>
                {!p.active && <Pill tone="bad">Inactive</Pill>}
                {verificationPill(p.verification_status)}
              </button>
            ))}
          </div>
        </>
      )}
      {open && <ProviderSheet id={open} config={config} onClose={() => { setOpen(null); load(); }} />}
    </div>
  );
}

function ProviderSheet({ id, config, onClose }: { id: string; config: AppConfig; onClose: () => void }) {
  const [p, setP] = useState<ProviderDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [days, setDays] = useState<number[]>([]); const [ws, setWs] = useState(""); const [we, setWe] = useState("");
  const [ld, setLd] = useState(""); const [lf, setLf] = useState("09:00"); const [lt, setLt] = useState("18:00"); const [lr, setLr] = useState("Leave");
  const [visits, setVisits] = useState<Visit[]>([]); const [vid, setVid] = useState("");
  const [elig, setElig] = useState<{ eligible: boolean; candidate: { score: number } | null; exclusion: { reason: string; detail: string } | null } | null>(null);

  const load = useCallback(() => api<ProviderDetail>(`/api/providers/${id}`).then((d) => { setP(d); setDays(d.work_days); setWs(d.work_start); setWe(d.work_end); }).catch((e: ApiError) => setErr(e.message)), [id]);
  useEffect(() => { load(); api<Visit[]>("/api/visits?status=unassigned").then(setVisits).catch(() => undefined); }, [load]);

  async function patch(body: object) {
    setErr(null);
    try { await api(`/api/providers/${id}`, { method: "PATCH", body }); await load(); } catch (e) { setErr((e as ApiError).message); }
  }
  async function addLeave() {
    setErr(null);
    if (!ld) { setErr("Choose a date for the leave block"); return; }
    try { await api(`/api/providers/${id}/leave`, { method: "POST", body: { start_at: `${ld}T${lf}:00+05:30`, end_at: `${ld}T${lt}:00+05:30`, reason: lr || "Leave" } }); await load(); } catch (e) { setErr((e as ApiError).message); }
  }
  async function check() {
    setElig(null); setErr(null);
    try { setElig(await api(`/api/providers/${id}/eligibility?visit_id=${vid}`)); } catch (e) { setErr((e as ApiError).message); }
  }

  return (
    <Sheet label="Provider profile" onClose={onClose}>
      {!p ? <div className="skel" /> : (
        <div className="stack">
          <div className="row"><div className={`avatar ${roleTile[p.role] ?? ""}`}>{initials(p.name)}</div><div><h2>{p.name}</h2><p className="cap">{p.id} · {p.role} · base near {p.home_locality} (approximate)</p></div></div>
          <div className="chips">{verificationPill(p.verification_status)}<Pill tone={credTone(p.credential_status)}>{credText[p.credential_status]}{p.credential_expiry ? `, ${p.credential_expiry}` : ""}</Pill><Pill tone={p.active ? "good" : "bad"}>{p.active ? "Active" : "Inactive"}</Pill></div>
          {err && <Note tone="bad">{err}</Note>}
          <div className="card flat"><KV rows={[
            ["Approved services", p.service_labels.join(", ") || "None"],
            ["Modes", p.service_modes.map((m) => config.mode_labels[m]).join(", ")],
            ["Localities", p.localities.join(", ")],
            ["Service radius", `${p.service_radius_km} km`],
            ["Visits completed", String(p.completed_visits)],
            ["On-time rate (raw)", pct(p.on_time_rate)],
            ["Cancellations", String(p.cancellations)],
            ["Travel buffer", `${p.buffer_minutes} min`],
            ["Daily visit cap", String(p.max_daily_visits)],
          ]} /></div>

          <div className="card flat form">
            <h3>Working hours</h3>
            <div className="chips" role="group" aria-label="Working days">{dayNames.map((d, i) => <button key={d} className="chipbtn" aria-pressed={days.includes(i)} onClick={() => setDays(days.includes(i) ? days.filter((x) => x !== i) : [...days, i].sort())}>{d}</button>)}</div>
            <div className="grid2"><div className="field"><label htmlFor="ws">Shift starts</label><input id="ws" type="time" className="input" value={ws} onChange={(e) => setWs(e.target.value)} /></div><div className="field"><label htmlFor="we">Shift ends</label><input id="we" type="time" className="input" value={we} onChange={(e) => setWe(e.target.value)} /></div></div>
            <div className="row"><button className="btn sm" onClick={() => patch({ work_days: days, work_start: ws, work_end: we })}>Save hours</button><button className="btn sm ghost" onClick={() => patch({ active: !p.active })}>{p.active ? "Deactivate provider" : "Activate provider"}</button></div>
            <p className="cap">Currently {workDays(p.work_days)}, {p.work_start} to {p.work_end}.</p>
          </div>

          <div className="card flat form">
            <h3>Leave and blocked time</h3>
            {p.leave_blocks.length === 0 && <p className="cap">No upcoming leave.</p>}
            {p.leave_blocks.map((l) => <div className="row" key={l.id} style={{ justifyContent: "space-between" }}><span>{l.reason}: {dateTimeOf(l.start_at)} to {timeOf(l.end_at)}</span><button className="link" onClick={async () => { await api(`/api/providers/${id}/leave/${l.id}`, { method: "DELETE" }); load(); }}>Remove</button></div>)}
            <div className="grid3"><div className="field"><label htmlFor="ld">Date</label><input id="ld" type="date" className="input" value={ld} onChange={(e) => setLd(e.target.value)} /></div><div className="field"><label htmlFor="lf">From</label><input id="lf" type="time" className="input" value={lf} onChange={(e) => setLf(e.target.value)} /></div><div className="field"><label htmlFor="lt">To</label><input id="lt" type="time" className="input" value={lt} onChange={(e) => setLt(e.target.value)} /></div></div>
            <div className="field"><label htmlFor="lr">Reason</label><input id="lr" className="input" value={lr} onChange={(e) => setLr(e.target.value)} /></div>
            <button className="btn sm" onClick={addLeave}>Add leave block</button>
          </div>

          <div className="card flat">
            <h3 style={{ marginBottom: 8 }}>Upcoming appointments ({p.appointments.length})</h3>
            {p.appointments.length === 0 ? <p className="cap">Nothing booked in the next week.</p> : <div className="list">{p.appointments.map((a) => <div key={a.id} className="s">{dateTimeOf(a.start_at)} to {timeOf(a.end_at)} · {a.service_label ?? "Visit"} · {a.mode === "on_call" ? "On call" : a.locality}</div>)}</div>}
          </div>

          <div className="card flat form">
            <h3>Would this provider be recommended?</h3>
            <p className="cap">Pick an unassigned visit to see whether this provider passes every check, or the exact reason they do not.</p>
            <div className="field"><label htmlFor="ev">Unassigned visit</label><select id="ev" className="input" value={vid} onChange={(e) => { setVid(e.target.value); setElig(null); }}><option value="">Choose a visit</option>{visits.map((v) => <option key={v.id} value={v.id}>#{v.id} {v.service_label}, {dateTimeOf(v.start_at)}, {v.locality}</option>)}</select></div>
            <button className="btn sm" disabled={!vid} onClick={check}>Check eligibility</button>
            {elig && (elig.eligible ? <Note tone="ok" title="Eligible.">Passes every check and would score {Math.round(elig.candidate!.score)} out of 100.</Note> : <Note tone="warn" title={elig.exclusion?.reason ?? "Not eligible."}>{elig.exclusion?.detail}</Note>)}
          </div>
          <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn ghost" onClick={onClose}>Close</button></div>
        </div>
      )}
    </Sheet>
  );
}
