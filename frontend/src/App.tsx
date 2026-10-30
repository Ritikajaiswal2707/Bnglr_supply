import { useCallback, useEffect, useState } from "react";
import { api, ApiError, setToken } from "./lib/api";
import type { AppConfig, Visit } from "./lib/types";
import Insights from "./components/Insights";
import Providers from "./components/Providers";
import SmartAssign from "./components/SmartAssign";
import Visits from "./components/Visits";
import { Note, Pill, RoutingBadge } from "./components/ui";

type Tab = "assign" | "providers" | "visits" | "insights";
const TABS: [Tab, string][] = [["assign", "Smart Assign"], ["providers", "Providers"], ["visits", "Visits"], ["insights", "Insights"]];

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("assign");
  const [prefill, setPrefill] = useState<Visit | null>(null);
  const [needPass, setNeedPass] = useState(false);
  const [pass, setPass] = useState("");

  const boot = useCallback(() => api<AppConfig>("/api/config").then((c) => { setConfig(c); setError(null); }).catch((e: ApiError) => setError(e.message)), []);
  useEffect(() => { boot(); const on = () => setNeedPass(true); window.addEventListener("smart-assign-auth", on); return () => window.removeEventListener("smart-assign-auth", on); }, [boot]);
  const clearPrefill = useCallback(() => setPrefill(null), []);

  if (error) return <div className="shell"><Note tone="bad" title="The app could not start.">{error}</Note></div>;
  if (!config) return <div className="shell"><div className="skel" /></div>;

  if (config.auth_required && (needPass || !sessionStorage.getItem("smart-assign-passcode"))) {
    return (
      <div className="shell" style={{ maxWidth: 440, paddingTop: 80 }}>
        <form className="card form" onSubmit={(e) => { e.preventDefault(); setToken(pass); setNeedPass(false); boot(); }}>
          <div className="avatar">PA</div>
          <h1>Smart Assign</h1>
          <p className="cap">This prototype is protected by a shared passcode.</p>
          <div className="field"><label htmlFor="pc">Passcode</label><input id="pc" type="password" className="input" value={pass} onChange={(e) => setPass(e.target.value)} autoFocus /></div>
          <button className="btn" type="submit" disabled={!pass}>Continue</button>
        </form>
      </div>
    );
  }

  return (
    <div className="shell">
      <header className="header">
        <div className="avatar" aria-hidden="true">PA</div>
        <div className="brand"><b>PeakAge Smart Assign</b><span>{config.tagline}</span></div>
        <div className="spacer" />
        <Pill tone="info">Demo data</Pill>
        <RoutingBadge routing={config.routing} />
      </header>
      {config.routing.configured_mode === "live" && !config.routing.live_configured && (
        <Note tone="bad" title="Live routing is not configured.">ROUTING_MODE is live but no Ola Maps key is set, so address lookup and travel times will fail. Set OLA_MAPS_API_KEY or switch ROUTING_MODE to demo.</Note>
      )}
      <nav className="tabs" aria-label="Sections">
        {TABS.map(([k, l]) => <button key={k} className="tab" aria-current={tab === k ? "page" : undefined} onClick={() => setTab(k)}>{l}</button>)}
      </nav>
      <main>
        {tab === "assign" && <SmartAssign config={config} prefill={prefill} clearPrefill={clearPrefill} goVisits={() => setTab("visits")} />}
        {tab === "providers" && <Providers config={config} />}
        {tab === "visits" && <Visits config={config} onPlan={(v) => { setPrefill(v); setTab("assign"); }} />}
        {tab === "insights" && <Insights config={config} />}
      </main>
    </div>
  );
}
