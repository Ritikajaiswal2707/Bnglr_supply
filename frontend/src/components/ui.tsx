import { ReactNode, useEffect, useRef } from "react";
import type { Recommendation } from "../lib/types";

export function Pill({ tone = "", children, title }: { tone?: "good" | "warn" | "bad" | "info" | "peach" | "dark" | ""; children: ReactNode; title?: string }) {
  return <span className={`pill ${tone}`} title={title}>{children}</span>;
}

export function Note({ tone = "", title, children }: { tone?: "warn" | "bad" | "ok" | "info" | ""; title?: string; children?: ReactNode }) {
  return (
    <div className={`note ${tone}`} role={tone === "bad" ? "alert" : "status"}>
      <div>{title && <b>{title} </b>}{children}</div>
    </div>
  );
}

export function Skeleton({ count = 3 }: { count?: number }) {
  return <div className="stack" aria-busy="true" aria-label="Finding providers">{Array.from({ length: count }, (_, i) => <div className="skel" key={i} style={{ height: i === 0 ? 220 : 150 }} />)}</div>;
}

export function Sheet({ label, onClose, children }: { label: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, [onClose]);
  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label={label} tabIndex={-1} ref={ref}>
        <div className="grab" />
        {children}
      </div>
    </div>
  );
}

export function KV({ rows }: { rows: [string, ReactNode][] }) {
  return <dl className="kv">{rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>;
}

export function RoutingBadge({ routing }: { routing: { mode: string; label: string } | Recommendation["routing"] }) {
  const live = routing.mode === "live";
  return <Pill tone={live ? "good" : "warn"} title={live ? "Road routing from Ola Maps" : "ETAs are simulated and do not use real traffic"}>{live ? "Live routing" : "Simulated"}: {routing.label}</Pill>;
}

export function verificationPill(status: string) {
  return <Pill tone={status === "verified" ? "good" : status === "pending" ? "warn" : "bad"}>{status === "verified" ? "Verified" : status === "pending" ? "Verification pending" : "Verification rejected"}</Pill>;
}
