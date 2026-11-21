/** Formatting helpers. `null` always renders as "unavailable", never as 0. */
export const UNAVAILABLE = "—";

export function num(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return UNAVAILABLE;
  return v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function ms(v: number | null | undefined): string {
  if (v === null || v === undefined) return UNAVAILABLE;
  return `${v < 10 ? v.toFixed(2) : v < 1000 ? v.toFixed(1) : num(v)} ms`;
}

export function pct(v: number | null | undefined, signed = false): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return UNAVAILABLE;
  const s = Math.abs(v) >= 1000 ? num(v) : v.toFixed(1);
  return `${signed && v > 0 ? "+" : ""}${s}%`;
}

export function bytes(v: number | null | undefined): string {
  if (v === null || v === undefined) return UNAVAILABLE;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = v;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return UNAVAILABLE;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? UNAVAILABLE : d.toLocaleString();
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function keyPattern(k: Record<string, unknown>): string {
  return `{ ${Object.entries(k).map(([f, d]) => `${f}: ${d}`).join(", ")} }`;
}

/** Parse a JSON textarea. Returns undefined for blank input; throws a readable Error otherwise. */
export function parseJson(text: string, label: string): Record<string, unknown> | undefined {
  if (!text.trim()) return undefined;
  try {
    const v = JSON.parse(text);
    if (typeof v !== "object" || v === null || Array.isArray(v)) throw new Error("must be a JSON object");
    return v as Record<string, unknown>;
  } catch (e) {
    throw new Error(`${label}: ${e instanceof Error ? e.message : "invalid JSON"}`);
  }
}

export function parseJsonArray(text: string, label: string): Record<string, unknown>[] | undefined {
  if (!text.trim()) return undefined;
  try {
    const v = JSON.parse(text);
    if (!Array.isArray(v)) throw new Error("must be a JSON array");
    return v as Record<string, unknown>[];
  } catch (e) {
    throw new Error(`${label}: ${e instanceof Error ? e.message : "invalid JSON"}`);
  }
}
