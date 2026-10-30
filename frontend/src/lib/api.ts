const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
const TOKEN_KEY = "smart-assign-passcode";

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export const getToken = () => sessionStorage.getItem(TOKEN_KEY) ?? "";
export const setToken = (t: string) => sessionStorage.setItem(TOKEN_KEY, t);

function messageFrom(body: unknown, fallback: string): { message: string; code?: string } {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return { message: d };
  if (Array.isArray(d)) return { message: d.map((x: { msg?: string }) => (x.msg ?? "").replace(/^Value error, /, "")).filter(Boolean).join(". ") || fallback };
  if (d && typeof d === "object") {
    const o = d as { message?: string; code?: string };
    return { message: o.message ?? fallback, code: o.code };
  }
  return { message: fallback };
}

export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["X-Access-Token"] = token;
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { method: init.method ?? "GET", headers, body: init.body === undefined ? undefined : JSON.stringify(init.body) });
  } catch {
    throw new ApiError(0, "Could not reach the server. Check your connection and try again.");
  }
  if (res.status === 204) return undefined as T;
  let data: unknown = null;
  try { data = await res.json(); } catch { /* non-JSON error body */ }
  if (!res.ok) {
    if (res.status === 401) window.dispatchEvent(new Event("smart-assign-auth"));
    const { message, code } = messageFrom(data, `Request failed (${res.status})`);
    throw new ApiError(res.status, message, code);
  }
  return data as T;
}
