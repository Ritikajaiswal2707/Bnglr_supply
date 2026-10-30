const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const dayNames = DAYS;

/** Times from the API carry +05:30. We show the Bengaluru wall-clock time as written, never the browser's zone. */
export function timeOf(iso: string | null | undefined): string {
  if (!iso) return "";
  const h = Number(iso.slice(11, 13));
  const m = iso.slice(14, 16);
  const suffix = h >= 12 ? "pm" : "am";
  return `${h % 12 === 0 ? 12 : h % 12}:${m} ${suffix}`;
}

export function dateOf(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return `${DAYS[(d.getUTCDay() + 6) % 7]} ${d.getUTCDate()} ${d.toLocaleString("en-GB", { month: "short", timeZone: "UTC" })} ${d.getUTCFullYear()}`;
}

export const dateTimeOf = (iso: string) => `${dateOf(iso)}, ${timeOf(iso)}`;

export const pct = (x: number) => `${Math.round(x * 100)}%`;

export function todayPlus(days: number, nowIso?: string): string {
  const base = nowIso ? new Date(`${nowIso.slice(0, 10)}T00:00:00Z`) : new Date();
  base.setUTCDate(base.getUTCDate() + days);
  return base.toISOString().slice(0, 10);
}

export function workDays(days: number[]): string {
  return days.map((d) => DAYS[d]).join(", ");
}

export const roleTile: Record<string, string> = {
  Nurse: "tile-green", Concierge: "tile-blue", Doctor: "tile-peach", Dietitian: "tile-yellow", "Fitness Coach": "tile-green", Counsellor: "tile-lilac",
};

export const initials = (name: string) => name.replace(/\(demo\)/i, "").trim().split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
