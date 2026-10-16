export type JobState = "preparing" | "running" | "blocked" | "review" | "complete";
export type StageState = "complete" | "active" | "pending";

export interface Level {
  level: number;
  completed: number;
  total: number;
}
export interface Group {
  id: string;
  label: string;
  completed: number;
  total: number;
  levels: Level[];
}
export interface Stage {
  id: string;
  label: string;
  state: StageState;
}
export interface DetailLink {
  label: string;
  value?: string;
  href?: string;
}
export interface ProjectStatus {
  schemaVersion: 1;
  job: { id: string; title: string; subtitle: string };
  state: JobState;
  stage: { id: string; label: string } | null;
  generatedAt: string | null;
  sourceGeneratedAt: string | null;
  censusGeneratedAt: string | null;
  collector: { ok: boolean; lastAttemptAt: string | null; error: string | null; staleAfterSeconds: number };
  progress: {
    unit: string;
    completed: number | null;
    total: number;
    basis: string;
    measuredAt: string | null;
    censusIntervalSeconds: number;
  };
  stages: Stage[];
  groups: Group[];
  completion: Record<string, boolean>;
  resources: Record<string, number | null>;
  billing: {
    gross: number | null;
    billed: number | null;
    credits: number | null;
    allowances: Record<string, number>;
    asOf: string | null;
    scope: string | null;
  } | null;
  details: DetailLink[];
  warnings: string[];
  coverageMaps: CoverageMap[];
}

export interface CoverageState {
  code: number;
  label: string;
  color: string;
  count: number;
}
export interface CoverageMap {
  id: string;
  label: string;
  width: number;
  height: number;
  image: string;
  states: CoverageState[];
  measuredAt: string | null;
  cellMeters: 128;
  provisional: true;
}

export const COVERAGE_MAX_MAPS = 2;
export const COVERAGE_MAX_STATES = 8;
export const COVERAGE_MAX_IMAGE_CHARS = 2 * 1024 * 1024;
export const COVERAGE_MAX_EDGE = 2048;
const PNG_DATA_URL = /^data:image\/png;base64,iVBORw0KGgo[A-Za-z0-9+/]*={0,2}$/;
const SIX_HEX = /^#[0-9a-fA-F]{6}$/;

// Check dimensions before Image decoding; a small compressed PNG must not
// allocate an unexpectedly large bitmap while the page validates its metadata.
function pngDimensionsMatch(image: string, width: number, height: number): boolean {
  try {
    const header = atob(image.slice("data:image/png;base64,".length, "data:image/png;base64,".length + 44));
    if (header.length < 33 || header.slice(12, 16) !== "IHDR") return false;
    const u32 = (i: number): number =>
      header.charCodeAt(i) * 2 ** 24 + header.charCodeAt(i + 1) * 2 ** 16 + header.charCodeAt(i + 2) * 256 + header.charCodeAt(i + 3);
    return u32(8) === 13 && u32(16) === width && u32(20) === height;
  } catch {
    return false;
  }
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown, d = ""): string => (typeof v === "string" ? v : d);
const strOrNull = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const count = (v: unknown): number | null => {
  const n = num(v);
  return n !== null && n >= 0 ? n : null;
};

export class ShapeError extends Error {}

function need<T>(v: T | null, what: string): T {
  if (v === null) throw new ShapeError(`Invalid status data: ${what}`);
  return v;
}

/** Validates untrusted JSON; throws ShapeError for malformed payloads. */
export function parseStatus(raw: unknown): ProjectStatus {
  if (!isObj(raw)) throw new ShapeError("Invalid status data: not an object");
  if (raw.schemaVersion !== 1) throw new ShapeError("Unsupported status schema version");
  if (!isObj(raw.job)) throw new ShapeError("Invalid status data: job missing");
  const job = {
    id: str(raw.job.id),
    title: need(strOrNull(raw.job.title), "job.title"),
    subtitle: str(raw.job.subtitle),
  };
  const state: JobState =
    raw.state === "preparing" || raw.state === "blocked" || raw.state === "review" || raw.state === "running" || raw.state === "complete"
      ? raw.state
      : (() => {
          throw new ShapeError("Invalid status data: state");
        })();
  if (!Array.isArray(raw.stages)) throw new ShapeError("Invalid status data: stages");
  const stages: Stage[] = raw.stages.flatMap((s): Stage[] => {
    if (!isObj(s) || typeof s.label !== "string") return [];
    const st = s.state === "complete" || s.state === "active" ? s.state : "pending";
    return [{ id: str(s.id, s.label), label: s.label, state: st }];
  });
  if (!isObj(raw.progress)) throw new ShapeError("Invalid status data: progress");
  const p = raw.progress;
  const progress = {
    unit: str(p.unit, "items"),
    completed: count(p.completed),
    total: need(count(p.total), "progress.total"),
    basis: str(p.basis),
    measuredAt: strOrNull(p.measuredAt),
    censusIntervalSeconds: count(p.censusIntervalSeconds) ?? 120,
  };
  if (!Array.isArray(raw.groups)) throw new ShapeError("Invalid status data: groups");
  const groups: Group[] = raw.groups.flatMap((g): Group[] => {
    if (!isObj(g) || typeof g.label !== "string") return [];
    const total = count(g.total);
    const completed = count(g.completed);
    if (total === null || completed === null) return [];
    const levels = Array.isArray(g.levels)
      ? g.levels.flatMap((l): Level[] => {
          if (!isObj(l)) return [];
          const lv = num(l.level);
          const lt = count(l.total);
          const lc = count(l.completed);
          return lv === null || lt === null || lc === null ? [] : [{ level: lv, completed: lc, total: lt }];
        })
      : [];
    return [{ id: str(g.id, g.label), label: g.label, completed, total, levels }];
  });
  const stage =
    isObj(raw.stage) && typeof raw.stage.label === "string"
      ? { id: str(raw.stage.id), label: raw.stage.label }
      : null;
  const c = isObj(raw.collector) ? raw.collector : {};
  const collector = {
    ok: c.ok === true,
    lastAttemptAt: strOrNull(c.lastAttemptAt),
    error: strOrNull(c.error),
    staleAfterSeconds: count(c.staleAfterSeconds) ?? 300,
  };
  const completion: Record<string, boolean> = {};
  if (isObj(raw.completion)) for (const [k, v] of Object.entries(raw.completion)) if (typeof v === "boolean") completion[k] = v;
  if (state === "complete" && completion.datasetComplete !== true) throw new ShapeError("Completion evidence is missing");
  const resources: Record<string, number | null> = {};
  if (isObj(raw.resources)) for (const [k, v] of Object.entries(raw.resources)) resources[k] = count(v);
  let billing: ProjectStatus["billing"] = null;
  if (isObj(raw.billing)) {
    const b = raw.billing;
    const allowances: Record<string, number> = {};
    if (isObj(b.allowances)) for (const [k, v] of Object.entries(b.allowances)) if (num(v) !== null) allowances[k] = v as number;
    billing = {
      gross: num(b.gross),
      billed: num(b.billed),
      credits: num(b.credits),
      allowances,
      asOf: strOrNull(b.asOf),
      scope: strOrNull(b.scope),
    };
  }
  const details: DetailLink[] = Array.isArray(raw.details)
    ? raw.details.flatMap((d): DetailLink[] => {
        if (!isObj(d) || typeof d.label !== "string") return [];
        return [{ label: d.label, value: typeof d.value === "string" ? d.value : undefined, href: safeHref(d.href) ?? undefined }];
      })
    : [];
  const warnings = Array.isArray(raw.warnings) ? raw.warnings.filter((w): w is string => typeof w === "string") : [];
  return {
    schemaVersion: 1,
    job,
    state,
    stage,
    generatedAt: strOrNull(raw.generatedAt),
    sourceGeneratedAt: strOrNull(raw.sourceGeneratedAt),
    censusGeneratedAt: strOrNull(raw.censusGeneratedAt),
    collector,
    progress,
    stages,
    groups,
    completion,
    resources,
    billing,
    details,
    warnings,
    coverageMaps: parseCoverageMaps(raw.coverageMaps),
  };
}

const edge = (v: unknown): number | null =>
  typeof v === "number" && Number.isInteger(v) && v >= 1 && v <= COVERAGE_MAX_EDGE ? v : null;

function parseCoverageMap(m: unknown): CoverageMap | null {
  if (!isObj(m)) return null;
  const id = strOrNull(m.id);
  const label = strOrNull(m.label);
  const width = edge(m.width);
  const height = edge(m.height);
  if (!id || !label || width === null || height === null || width * height > COVERAGE_MAX_EDGE * COVERAGE_MAX_EDGE) return null;
  const image = m.image;
  if (typeof image !== "string" || image.length > COVERAGE_MAX_IMAGE_CHARS || !PNG_DATA_URL.test(image) || !pngDimensionsMatch(image, width, height)) return null;
  if (m.cellMeters !== 128 || m.provisional !== true) return null;
  if (!Array.isArray(m.states) || m.states.length > COVERAGE_MAX_STATES) return null;
  const states: CoverageState[] = [];
  for (const s of m.states) {
    if (!isObj(s)) return null;
    const code = s.code;
    const stateLabel = strOrNull(s.label);
    const n = count(s.count);
    if (typeof code !== "number" || !Number.isInteger(code) || code < 0 || code > 255) return null;
    if (!stateLabel || n === null || !Number.isInteger(n) || n > width * height || typeof s.color !== "string" || !SIX_HEX.test(s.color)) return null;
    if (states.some((existing) => existing.code === code || existing.color === (s.color as string).toLowerCase())) return null;
    states.push({ code, label: stateLabel, color: s.color.toLowerCase(), count: n });
  }
  if (states.reduce((sum, state) => sum + state.count, 0) !== width * height) return null;
  return { id, label, width, height, image, states, measuredAt: strOrNull(m.measuredAt), cellMeters: 128, provisional: true };
}

/** Optional section: absent or malformed maps never invalidate the rest of the status. */
function parseCoverageMaps(raw: unknown): CoverageMap[] {
  if (!Array.isArray(raw)) return [];
  const maps: CoverageMap[] = [];
  for (const m of raw.slice(0, COVERAGE_MAX_MAPS)) {
    const parsed = parseCoverageMap(m);
    if (parsed && !maps.some((x) => x.id === parsed.id)) maps.push(parsed);
  }
  return maps;
}

/** Only http(s) absolute URLs or root-relative paths are linkable. */
export function safeHref(v: unknown): string | null {
  if (typeof v !== "string" || !v) return null;
  if (/[\u0000-\u0020\\]/.test(v)) return null;
  if (v.startsWith("/") && !v.startsWith("//") && !v.startsWith("/\\")) return v;
  try {
    const u = new URL(v);
    return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
  } catch {
    return null;
  }
}

const nf = new Intl.NumberFormat("en-US");
export const fmtInt = (n: number): string => nf.format(Math.round(n));

export function fmtBytes(n: number | null): string {
  if (n === null) return "Not reported";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 || v >= 100 ? 0 : 1)} ${units[i]}`;
}

export function fmtMoney(n: number | null): string {
  return n === null ? "Not reported" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(n);
}

export function fmtTime(iso: string | null): string {
  if (!iso) return "Unknown";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "Unknown"
    : d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function ageSeconds(iso: string | null, now = Date.now()): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : Math.max(0, (now - t) / 1000);
}

export function fmtAge(sec: number | null): string {
  if (sec === null) return "unknown age";
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} h ${Math.floor((sec % 3600) / 60)} min ago`;
  return `${Math.floor(sec / 86400)} d ago`;
}

export function percent(completed: number, total: number): number {
  return total > 0 ? Math.min(100, Math.max(0, (completed / total) * 100)) : 0;
}

export function fmtPercent(completed: number, total: number): string {
  const p = percent(completed, total);
  return p > 0 && p < 10 ? `${p.toFixed(1)}%` : `${Math.floor(p)}%`;
}

/** File rewrite time cannot make old source evidence fresh. */
export function isStale(s: ProjectStatus, now = Date.now()): boolean {
  const sourceAge = ageSeconds(s.sourceGeneratedAt, now);
  const censusAge = ageSeconds(s.censusGeneratedAt, now);
  return sourceAge === null || sourceAge > 90 || censusAge === null || censusAge > s.collector.staleAfterSeconds;
}
