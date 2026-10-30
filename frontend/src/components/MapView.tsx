import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { Candidate } from "../lib/types";

export const RANK_COLORS = [
  { bg: "#F9C6B2", fg: "#141414" },
  { bg: "#CDE5EF", fg: "#141414" },
  { bg: "#D8EAB4", fg: "#141414" },
];

interface Props {
  destination: { lat: number; lng: number } | null;
  candidates: Candidate[];
  pickMode: boolean;
  onPick: (lat: number, lng: number) => void;
}

const icon = (html: string, size: number) => L.divIcon({ html, className: "", iconSize: [size, size], iconAnchor: [size / 2, size / 2] });

export default function MapView({ destination, candidates, pickMode, onPick }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const pickRef = useRef({ pickMode, onPick });
  pickRef.current = { pickMode, onPick };

  useEffect(() => {
    if (!el.current || map.current) return;
    const m = L.map(el.current, { zoomControl: true, attributionControl: true }).setView([12.9716, 77.5946], 11);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" }).addTo(m);
    m.on("click", (e: L.LeafletMouseEvent) => pickRef.current.pickMode && pickRef.current.onPick(e.latlng.lat, e.latlng.lng));
    layer.current = L.layerGroup().addTo(m);
    map.current = m;
    return () => { m.remove(); map.current = null; };
  }, []);

  useEffect(() => {
    const m = map.current, g = layer.current;
    if (!m || !g) return;
    g.clearLayers();
    const pts: L.LatLngTuple[] = [];
    if (destination) {
      const mk = L.marker([destination.lat, destination.lng], {
        icon: icon('<div class="pin" style="background:#141414;color:#fff" aria-label="Parent">P</div>', 30), draggable: true, title: "Parent location. Drag to correct.",
      }).addTo(g);
      mk.on("dragend", () => { const p = mk.getLatLng(); pickRef.current.onPick(p.lat, p.lng); });
      pts.push([destination.lat, destination.lng]);
    }
    candidates.forEach((c, i) => {
      const col = RANK_COLORS[i % 3];
      if (c.origin.lat != null && c.origin.lng != null) {
        L.marker([c.origin.lat, c.origin.lng], { icon: icon(`<div class="pin origin" style="background:${col.bg};border-color:#141414"></div>`, 20), title: `${c.name}: ${c.origin.label}` })
          .bindTooltip(`${c.name}<br/>${c.origin.label}`).addTo(g);
        L.marker([c.origin.lat, c.origin.lng], { icon: icon(`<div class="pin" style="background:${col.bg};color:${col.fg};margin-top:-26px">${c.rank}</div>`, 30), title: c.name }).addTo(g);
        pts.push([c.origin.lat, c.origin.lng]);
      }
    });
    if (pts.length > 1) m.fitBounds(L.latLngBounds(pts), { padding: [48, 48], maxZoom: 14 });
    else if (pts.length === 1) m.setView(pts[0], 14);
  }, [destination, candidates]);

  useEffect(() => { if (el.current) el.current.style.cursor = pickMode ? "crosshair" : ""; map.current?.getContainer().classList.toggle("picking", pickMode); }, [pickMode]);

  return <div className="mapbox" ref={el} role="application" aria-label="Map of the parent location and recommended providers" />;
}

export function MapLegend({ simulated, hasRoutes }: { simulated: boolean; hasRoutes: boolean }) {
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="legend">
        <span><span className="dot" style={{ background: "#141414" }} />Parent</span>
        {RANK_COLORS.map((c, i) => <span key={i}><span className="dot" style={{ background: c.bg, boxShadow: "inset 0 0 0 2px #141414" }} />{i === 0 ? "Best match" : `Alternative ${i + 1}`} (number) and where they travel from (square)</span>)}
      </div>
      <p className="cap">
        {simulated ? "Distances and ETAs are simulated, not live traffic. " : "Distances and ETAs come from live road routing. "}
        Route lines are not drawn{hasRoutes ? ", because no route geometry is requested" : ""}. Provider origins are approximate base areas or the location of their previous visit.
      </p>
    </div>
  );
}
