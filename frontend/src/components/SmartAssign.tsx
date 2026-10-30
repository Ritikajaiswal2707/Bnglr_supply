import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../lib/api";
import { dateTimeOf, todayPlus } from "../lib/format";
import type { AppConfig, Candidate, GeocodeResponse, Mode, Parent, Recommendation, Visit } from "../lib/types";
import CandidateCard from "./CandidateCard";
import ConfirmSheet from "./ConfirmSheet";
import MapView, { MapLegend } from "./MapView";
import { Note, Pill, RoutingBadge, Skeleton } from "./ui";

interface Loc { lat: number; lng: number; label: string; locality: string | null; source: "demo" | "ola" | "manual"; forAddress: string }
interface Form { parentId: string; address: string; service: string; mode: Mode; date: string; time: string; duration: number; caps: string[] }


function nearestLocality(cfg: AppConfig, lat: number, lng: number): string | null {
  let best: string | null = null, bd = Infinity;
  for (const l of cfg.localities) {
    const d = Math.hypot((l.lat - lat) * 111, (l.lng - lng) * 108);
    if (d < bd) { bd = d; best = l.name; }
  }
  return bd <= 6 ? best : null;
}

interface Props { config: AppConfig; prefill: Visit | null; clearPrefill: () => void; goVisits: () => void }

export default function SmartAssign({ config, prefill, clearPrefill, goVisits }: Props) {
  const first = config.services[0];
  const [parents, setParents] = useState<Parent[]>([]);
  const [f, setF] = useState<Form>({ parentId: "", address: "", service: first.key, mode: first.modes[0], date: todayPlus(1, config.now), time: "11:00", duration: first.minutes, caps: [] });
  const [errs, setErrs] = useState<Partial<Record<keyof Form, string>>>({});
  const [loc, setLoc] = useState<Loc | null>(null);
  const [geo, setGeo] = useState<GeocodeResponse | null>(null);
  const [pick, setPick] = useState(false);
  const [visit, setVisit] = useState<Visit | null>(null);
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [ranKey, setRanKey] = useState("");
  const [busy, setBusy] = useState<"" | "geocoding" | "searching">("");
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Candidate | null>(null);
  const [done, setDone] = useState<{ provider: string; score: number; notification: string } | null>(null);

  const svc = useMemo(() => config.services.find((s) => s.key === f.service)!, [config, f.service]);
  const key = JSON.stringify([f, loc?.lat, loc?.lng]);
  const stale = rec !== null && key !== ranKey;

  useEffect(() => { api<Parent[]>("/api/parents").then(setParents).catch(() => undefined); }, []);

  useEffect(() => {
    if (!prefill) return;
    const svcDef = config.services.find((s) => s.key === prefill.service) ?? first;
    setF({ parentId: prefill.parent_id, address: prefill.address, service: svcDef.key, mode: prefill.mode, date: prefill.start_at.slice(0, 10), time: prefill.start_at.slice(11, 16), duration: prefill.duration_minutes, caps: prefill.required_capabilities });
    setLoc({ lat: prefill.lat, lng: prefill.lng, label: prefill.address, locality: prefill.locality, source: prefill.geocode_source as Loc["source"], forAddress: prefill.address.trim() });
    setVisit(prefill); setRec(null); setGeo(null); setDone(null); setError(null);
    clearPrefill();
  }, [prefill, config, first, clearPrefill]);

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((p) => ({ ...p, [k]: v }));

  function onService(k: string) {
    const s = config.services.find((x) => x.key === k)!;
    setF((p) => ({ ...p, service: k, mode: s.modes.includes(p.mode) ? p.mode : s.modes[0], duration: s.minutes, caps: [] }));
  }

  function onParent(id: string) {
    const p = parents.find((x) => x.id === id);
    setF((prev) => ({ ...prev, parentId: id, address: p ? p.address : prev.address }));
    setLoc(null); setGeo(null);
  }

  function validate(): boolean {
    const e: Partial<Record<keyof Form, string>> = {};
    if (!f.parentId) e.parentId = "Choose a parent";
    if (f.address.trim().length < 3) e.address = "Enter the parent's address";
    if (!f.date) e.date = "Choose a date"; else if (f.date < config.now.slice(0, 10)) e.date = "The date is in the past";
    if (!f.time) e.time = "Choose a start time";
    else if (f.date === config.now.slice(0, 10) && f.time < config.now.slice(11, 16)) e.time = "The time is in the past";
    if (!(f.duration >= 10 && f.duration <= 480)) e.duration = "Use 10 to 480 minutes";
    setErrs(e);
    return Object.keys(e).length === 0;
  }

  async function onFind() {
    setError(null); setDone(null);
    if (!validate()) return;
    if (!loc || loc.forAddress !== f.address.trim()) {
      setBusy("geocoding"); setRec(null);
      try {
        const g = await api<GeocodeResponse>("/api/geocode", { method: "POST", body: { query: f.address.trim() } });
        setGeo(g);
        if (g.status !== "ok") setPick(true);
      } catch (e) { setError((e as ApiError).message); }
      finally { setBusy(""); }
      return;
    }
    await run(loc);
  }

  async function chooseAddress(c: GeocodeResponse["candidates"][number]) {
    const l: Loc = { lat: c.lat, lng: c.lng, label: c.label, locality: c.locality, source: c.source as Loc["source"], forAddress: f.address.trim() };
    setLoc(l); setGeo(null); setPick(false);
    await run(l);
  }

  const onMapPick = useCallback((lat: number, lng: number) => {
    setLoc({ lat, lng, label: f.address.trim(), locality: nearestLocality(config, lat, lng), source: "manual", forAddress: f.address.trim() });
    setGeo(null); setRec(null);
  }, [config, f.address]);

  async function run(l: Loc) {
    setBusy("searching"); setError(null); setRec(null);
    const body = { parent_id: f.parentId, service: f.service, mode: f.mode, address: f.address.trim(), lat: l.lat, lng: l.lng, locality: l.locality,
      geocode_source: l.source, date: f.date, start_time: f.time, duration_minutes: f.duration, required_capabilities: f.caps };
    try {
      let v: Visit;
      if (visit && visit.status === "unassigned") v = await api<Visit>(`/api/visits/${visit.id}`, { method: "PATCH", body });
      else v = await api<Visit>("/api/visits", { method: "POST", body });
      setVisit(v);
      const r = await api<Recommendation>(`/api/visits/${v.id}/recommendations`, { method: "POST" });
      setRec(r);
      setRanKey(JSON.stringify([f, l.lat, l.lng]));
    } catch (e) { setError((e as ApiError).message); }
    finally { setBusy(""); }
  }

  function reset() {
    setVisit(null); setRec(null); setLoc(null); setGeo(null); setDone(null); setChosen(null); setError(null); setPick(false);
    setF((p) => ({ ...p, address: "", parentId: "" }));
  }

  const dest = loc ? { lat: loc.lat, lng: loc.lng } : null;
  const simulated = (rec?.routing.mode ?? config.routing.mode) !== "live";
  const groups = useMemo(() => {
    const m = new Map<string, { reason: string; items: Recommendation["excluded"] }>();
    for (const e of rec?.excluded ?? []) { if (!m.has(e.code)) m.set(e.code, { reason: e.reason, items: [] }); m.get(e.code)!.items.push(e); }
    return [...m.entries()].sort((a, b) => (a[0] === "ROLE_MISMATCH" ? 1 : b[0] === "ROLE_MISMATCH" ? -1 : b[1].items.length - a[1].items.length));
  }, [rec]);

  return (
    <div className="layout">
      <div className="stack">
        <section className="card" aria-labelledby="req">
          <h2 id="req" style={{ marginBottom: 4 }}>Visit request</h2>
          <p className="cap" style={{ marginBottom: 14 }}>Enter the visit once. Smart Assign checks every provider and shows a shortlist.</p>
          <div className="form">
            <div className="field">
              <label htmlFor="parent">Parent</label>
              <select id="parent" className="input" value={f.parentId} aria-invalid={!!errs.parentId} onChange={(e) => onParent(e.target.value)}>
                <option value="">Choose a demo parent</option>
                {parents.map((p) => <option key={p.id} value={p.id}>{p.name}, {p.locality}</option>)}
              </select>
              {errs.parentId && <span className="err">{errs.parentId}</span>}
            </div>
            <div className="field">
              <label htmlFor="addr">Visit address</label>
              <input id="addr" className="input" value={f.address} aria-invalid={!!errs.address} placeholder="House number, street, locality" onChange={(e) => { set("address", e.target.value); }} onKeyDown={(e) => e.key === "Enter" && onFind()} />
              {errs.address && <span className="err">{errs.address}</span>}
              {loc && loc.forAddress === f.address.trim() && !geo && (
                <div className="rank-line">
                  <Pill tone="good">Address confirmed</Pill>
                  <span className="cap">{loc.label}{loc.locality ? `, ${loc.locality}` : ""}{loc.source === "demo" ? " (approximate)" : loc.source === "manual" ? " (pin set by hand)" : ""}</span>
                  <button className="link cap" onClick={() => setPick((p) => !p)}>{pick ? "Done adjusting" : "Adjust pin"}</button>
                </div>
              )}
            </div>
            <div className="field">
              <label htmlFor="svc">Service</label>
              <select id="svc" className="input" value={f.service} onChange={(e) => onService(e.target.value)}>
                {config.services.map((s) => <option key={s.key} value={s.key}>{s.label} ({s.role})</option>)}
              </select>
            </div>
            {svc.modes.length > 1 && (
              <div className="field">
                <span className="label" id="modelbl">Service mode</span>
                <div className="seg" role="group" aria-labelledby="modelbl">
                  {svc.modes.map((m) => <button key={m} aria-pressed={f.mode === m} onClick={() => set("mode", m)}>{config.mode_labels[m]}</button>)}
                </div>
              </div>
            )}
            <div className="grid3">
              <div className="field"><label htmlFor="date">Date</label><input id="date" type="date" className="input" value={f.date} min={config.now.slice(0, 10)} aria-invalid={!!errs.date} onChange={(e) => set("date", e.target.value)} />{errs.date && <span className="err">{errs.date}</span>}</div>
              <div className="field"><label htmlFor="time">Start time</label><input id="time" type="time" step={300} className="input" value={f.time} aria-invalid={!!errs.time} onChange={(e) => set("time", e.target.value)} />{errs.time && <span className="err">{errs.time}</span>}</div>
              <div className="field"><label htmlFor="dur">Minutes</label><input id="dur" type="number" min={10} max={480} step={5} className="input" value={f.duration} aria-invalid={!!errs.duration} onChange={(e) => set("duration", Number(e.target.value))} />{errs.duration && <span className="err">{errs.duration}</span>}</div>
            </div>
            <div className="field">
              <span className="label" id="caplbl">Specific skills needed (optional)</span>
              <div className="chips" role="group" aria-labelledby="caplbl">
                {svc.capabilities.map((c) => (
                  <button key={c.key} className="chipbtn" aria-pressed={f.caps.includes(c.key)} onClick={() => set("caps", f.caps.includes(c.key) ? f.caps.filter((x) => x !== c.key) : [...f.caps, c.key])}>{c.label}</button>
                ))}
              </div>
            </div>
            <button className="btn block" onClick={onFind} disabled={busy !== ""}>{busy === "geocoding" ? "Looking up address" : busy === "searching" ? "Finding providers" : "Find providers"}</button>
            {error && <Note tone="bad" title="Something went wrong.">{error}</Note>}
          </div>
        </section>

        {geo && (
          <section className="card" aria-live="polite">
            {geo.status === "ok" ? (
              <>
                <h3 style={{ marginBottom: 4 }}>{geo.candidates.length > 1 ? "Which address is this?" : "Is this the right address?"}</h3>
                <p className="cap" style={{ marginBottom: 10 }}>Pick one to continue. We never choose an address for you.</p>
                <div className="list">
                  {geo.candidates.map((c) => (
                    <button className="pick" key={c.label + c.lat} onClick={() => chooseAddress(c)}>
                      <b>{c.label}</b>
                      <span className="cap">{c.locality ?? "Locality unknown"}{c.approximate ? ", approximate location from the demo gazetteer" : ""}</span>
                    </button>
                  ))}
                  <button className="pick" onClick={() => { setGeo(null); setPick(true); }}><b>None of these</b><span className="cap">Place the pin on the map yourself</span></button>
                </div>
              </>
            ) : (
              <Note tone={geo.status === "failed" ? "bad" : "warn"} title={geo.status === "failed" ? "Address lookup failed." : "Address not found."}>
                {geo.error} Click the map to place the pin by hand, then choose Find providers.
              </Note>
            )}
          </section>
        )}
        {pick && !geo && <Note tone="info">Pin mode is on. Click the map or drag the black P marker to the exact spot.</Note>}

        {busy === "searching" && <Skeleton />}

        {done && (
          <section className="card tile-green" aria-live="polite">
            <Pill tone="good">Assigned</Pill>
            <h2 style={{ margin: "10px 0 4px" }}>{done.provider} is assigned</h2>
            <p>{visit && dateTimeOf(visit.start_at)}. Match score {Math.round(done.score)} out of 100. The provider's schedule is updated.</p>
            <p className="cap" style={{ marginTop: 6, color: "var(--on-green)" }}>{done.notification}</p>
            <div className="row" style={{ marginTop: 14 }}><button className="btn" onClick={reset}>Plan another visit</button><button className="btn ghost" onClick={goVisits}>View visits</button></div>
          </section>
        )}

        {rec && !done && busy === "" && (
          <section className="stack" aria-live="polite">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h2>Recommended providers</h2>
              <RoutingBadge routing={rec.routing} />
            </div>
            {stale && <Note tone="warn" title="Details changed.">Run Find providers again before choosing someone.</Note>}
            {rec.outcome === "routing_failed" && <Note tone="bad" title="Travel times unavailable.">{rec.message}</Note>}
            {rec.outcome === "no_feasible_provider" && <Note tone="warn" title="No feasible provider found.">{rec.message?.replace(/^No feasible provider found\.\s*/, "")}</Note>}
            {rec.outcome === "ok" && rec.message && <Note tone="info">{rec.message}</Note>}
            {rec.routing.status === "partial" && <Note tone="warn" title="Some routes failed.">{rec.routing.error}. Providers whose travel could not be checked were left out.</Note>}
            {rec.candidates.map((c) => <CandidateCard key={c.provider_id} c={c} routingSimulated={simulated} onSelect={(x) => !stale && setChosen(x)} />)}
            <p className="cap">Evaluated {rec.providers_evaluated} providers in {rec.latency_ms} ms. {rec.feasible_count} passed every check.</p>
            <details className="card flat">
              <summary>Unavailable providers ({rec.excluded.length}), for debugging</summary>
              <div className="stack" style={{ marginTop: 10 }}>
                {groups.map(([code, g]) => (
                  <details key={code}>
                    <summary>{g.reason} ({g.items.length})</summary>
                    <ul className="reasons" style={{ marginTop: 6 }}>{g.items.map((e) => <li key={e.provider_id}>{e.name}: <span className="cap">{e.detail}</span></li>)}</ul>
                  </details>
                ))}
              </div>
            </details>
          </section>
        )}
      </div>

      <aside className="stack sticky" aria-label="Map">
        <MapView destination={dest} candidates={rec && !stale && !done ? rec.candidates : []} pickMode={pick} onPick={onMapPick} />
        <MapLegend simulated={simulated} hasRoutes={false} />
      </aside>

      {chosen && visit && rec && (
        <ConfirmSheet visit={visit} rec={rec} cand={chosen} onClose={() => setChosen(null)}
          onDone={(r) => { setChosen(null); setDone(r); }}
          onRerun={() => { setChosen(null); if (loc) run(loc); }} />
      )}
    </div>
  );
}
