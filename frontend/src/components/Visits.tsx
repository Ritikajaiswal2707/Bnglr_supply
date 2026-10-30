import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import { dateTimeOf, todayPlus } from "../lib/format";
import type { AppConfig, Parent, Visit } from "../lib/types";
import { Note, Pill, Sheet } from "./ui";

interface Hist { id: number; provider_name: string; status: string; assigned_by: string; assigned_at: string; ended_reason: string | null; score: number | null; rank: number | null; override_reason: string | null }

export default function Visits({ config, onPlan }: { config: AppConfig; onPlan: (v: Visit) => void }) {
  const [tab, setTab] = useState<"" | "unassigned" | "assigned">("");
  const [rows, setRows] = useState<Visit[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [hist, setHist] = useState<{ v: Visit; h: Hist[] } | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(() => api<Visit[]>(`/api/visits${tab ? `?status=${tab}` : ""}`).then((r) => { setRows(r); setErr(null); }).catch((e: ApiError) => setErr(e.message)), [tab]);
  useEffect(() => { load(); }, [load]);

  async function unassign(v: Visit) {
    try { await api(`/api/visits/${v.id}/unassign`, { method: "POST", body: {} }); load(); } catch (e) { setErr((e as ApiError).message); }
  }
  async function history(v: Visit) { setHist({ v, h: await api<Hist[]>(`/api/visits/${v.id}/assignment-history`) }); }

  return (
    <div className="stack">
      <section className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2>Visits</h2>
          <div className="row">
            <div className="seg" role="group" aria-label="Filter visits">
              {([["", "All"], ["unassigned", "Unassigned"], ["assigned", "Assigned"]] as const).map(([k, l]) => <button key={k} aria-pressed={tab === k} onClick={() => setTab(k)}>{l}</button>)}
            </div>
            <button className="btn sm" onClick={() => setAdding(true)}>Add demo visit</button>
          </div>
        </div>
      </section>
      {err && <Note tone="bad">{err}</Note>}
      {rows === null ? <div className="skel" /> : rows.length === 0 ? <div className="card empty">No visits here yet.</div> : (
        <div className="list">
          {rows.map((v) => (
            <div className="item block" key={v.id}>
              <div className="grow">
                <div className="t">{v.service_label} <span className="cap">· {v.mode === "on_call" ? "On call" : v.locality ?? "Bengaluru"}</span></div>
                <div className="s">#{v.id} · {dateTimeOf(v.start_at)} · {v.duration_minutes} min · {v.parent_name}</div>
                <div className="rank-line" style={{ marginTop: 8 }}>
                  {v.status === "assigned" ? <Pill tone="good">Assigned to {v.assignment?.provider_name}</Pill> : <Pill tone="warn">Unassigned</Pill>}
                  {v.assignment?.rank && <Pill>Rank {v.assignment.rank}{v.assignment.score != null ? `, score ${Math.round(v.assignment.score)}` : ""}</Pill>}
                </div>
              </div>
              <div className="stack" style={{ gap: 6 }}>
                {v.status === "unassigned" && <button className="btn sm" onClick={() => onPlan(v)}>Find provider</button>}
                {v.status === "assigned" && <button className="btn sm ghost" onClick={() => unassign(v)}>Unassign</button>}
                <button className="btn sm ghost" onClick={() => history(v)}>History</button>
              </div>
            </div>
          ))}
        </div>
      )}
      {hist && (
        <Sheet label="Assignment history" onClose={() => setHist(null)}>
          <h2 style={{ marginBottom: 10 }}>History for visit #{hist.v.id}</h2>
          {hist.h.length === 0 ? <p className="cap">No assignments yet.</p> : <div className="list">{hist.h.map((h) => (
            <div className="card flat" key={h.id}><b>{h.provider_name}</b> <Pill tone={h.status === "active" ? "good" : ""}>{h.status}</Pill>
              <p className="cap">By {h.assigned_by}, {dateTimeOf(h.assigned_at)}{h.rank ? `, rank ${h.rank}` : ""}{h.score != null ? `, score ${Math.round(h.score)}` : ""}</p>
              {h.override_reason && <p className="cap">Reason for choosing this provider: {h.override_reason}</p>}
              {h.ended_reason && <p className="cap">Ended: {h.ended_reason}</p>}</div>))}</div>}
          <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}><button className="btn ghost" onClick={() => setHist(null)}>Close</button></div>
        </Sheet>
      )}
      {adding && <AddVisit config={config} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); load(); }} />}
    </div>
  );
}

function AddVisit({ config, onClose, onSaved }: { config: AppConfig; onClose: () => void; onSaved: () => void }) {
  const [parents, setParents] = useState<Parent[]>([]);
  const [pid, setPid] = useState(""); const [svc, setSvc] = useState(config.services[0].key);
  const [date, setDate] = useState(todayPlus(2, config.now)); const [time, setTime] = useState("11:00");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Parent[]>("/api/parents").then(setParents); }, []);
  async function save() {
    const p = parents.find((x) => x.id === pid); const s = config.services.find((x) => x.key === svc)!;
    if (!p) { setErr("Choose a parent"); return; }
    try {
      await api("/api/visits", { method: "POST", body: { parent_id: p.id, service: svc, mode: s.modes[0], address: p.address, lat: p.lat, lng: p.lng, locality: p.locality, geocode_source: "demo", date, start_time: time, duration_minutes: s.minutes, required_capabilities: [] } });
      onSaved();
    } catch (e) { setErr((e as ApiError).message); }
  }
  return (
    <Sheet label="Add demo visit" onClose={onClose}>
      <h2 style={{ marginBottom: 4 }}>Add demo visit</h2>
      <p className="cap" style={{ marginBottom: 12 }}>Creates an unassigned visit at the parent's address on file, without running recommendations.</p>
      <div className="form">
        <div className="field"><label htmlFor="ap">Parent</label><select id="ap" className="input" value={pid} onChange={(e) => setPid(e.target.value)}><option value="">Choose a demo parent</option>{parents.map((p) => <option key={p.id} value={p.id}>{p.name}, {p.locality}</option>)}</select></div>
        <div className="field"><label htmlFor="as">Service</label><select id="as" className="input" value={svc} onChange={(e) => setSvc(e.target.value)}>{config.services.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</select></div>
        <div className="grid2"><div className="field"><label htmlFor="ad">Date</label><input id="ad" type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} /></div><div className="field"><label htmlFor="at">Start time</label><input id="at" type="time" className="input" value={time} onChange={(e) => setTime(e.target.value)} /></div></div>
        {err && <Note tone="bad">{err}</Note>}
        <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn ghost" onClick={onClose}>Cancel</button><button className="btn" onClick={save}>Add visit</button></div>
      </div>
    </Sheet>
  );
}
