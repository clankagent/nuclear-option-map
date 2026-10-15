import {
  ageSeconds,
  fmtAge,
  fmtBytes,
  fmtInt,
  fmtMoney,
  fmtPercent,
  fmtTime,
  isStale,
  parseStatus,
  percent,
  safeHref,
  type Group,
  type ProjectStatus,
} from "./model";

const STATUS_URL = "/maps/render-status/project-status.json";
const POLL_MS = 15_000;
const TIMEOUT_MS = 10_000;

const $ = <T extends HTMLElement>(id: string): T => {
  const e = document.getElementById(id);
  if (!e) throw new Error(`missing #${id}`);
  return e as T;
};

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  opts: { cls?: string; text?: string; attrs?: Record<string, string> } = {},
  ...children: (Node | null)[]
): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (opts.cls) e.className = opts.cls;
  if (opts.text !== undefined) e.textContent = opts.text;
  for (const [k, v] of Object.entries(opts.attrs ?? {})) e.setAttribute(k, v);
  for (const c of children) if (c) e.append(c);
  return e;
}

const refs = {
  title: $("job-title"),
  subtitle: $("job-subtitle"),
  refresh: $<HTMLButtonElement>("refresh"),
  freshness: $("freshness"),
  banner: $("banner"),
  pill: $("state-pill"),
  stage: $("stage-label"),
  pct: $("pct"),
  counts: $("counts"),
  bar: $("bar"),
  fill: $("bar-fill"),
  basis: $("basis"),
  completion: $("completion"),
  groups: $("groups"),
  stages: $("stages"),
  resources: $("resources"),
  billingSec: $("billing-sec"),
  billing: $("billing"),
  billingNote: $("billing-note"),
  warnSec: $("warn-sec"),
  warnings: $("warnings"),
  source: $("source"),
};

let current: ProjectStatus | null = null;
let fetchError: string | null = null;
let lastOkAt: number | null = null;
let inFlight = false;
let timer: number | undefined;
let lastDigest = "";
const openGroups = new Set<string>();

const STATE_TEXT: Record<string, string> = { running: "Running", blocked: "Needs attention", review: "Ready for review", complete: "Complete" };
const LABELS: Record<string, string> = {
  diskFreeBytes: "Disk free",
  memoryCurrentBytes: "Memory now",
  memoryPeakBytes: "Memory peak",
  memoryLimitBytes: "Memory limit",
  buildMarkerPresent: "Build verified",
  derivedAuditPresent: "Zoom tiles verified",
  appearanceAccepted: "Appearance reviewed",
  published: "Review copy available",
  datasetComplete: "Ready for use",
};
const humanize = (k: string): string =>
  LABELS[k] ?? k.replace(/([A-Z])/g, " $1").replace(/^./, (c) => c.toUpperCase());

function kv(label: string, value: string, cls = ""): HTMLElement {
  return el("div", {}, el("dt", { text: label }), el("dd", { text: value, cls }));
}

function setBanner(msg: string | null, error = false): void {
  if (msg === null) {
    refs.banner.hidden = true;
    return;
  }
  if (refs.banner.textContent !== msg) refs.banner.textContent = msg;
  refs.banner.className = error ? "banner error" : "banner";
  refs.banner.hidden = false;
}

function renderGroups(groups: Group[]): void {
  const focused = document.activeElement instanceof HTMLElement ? document.activeElement.dataset.gid : undefined;
  refs.groups.replaceChildren();
  if (!groups.length) {
    refs.groups.append(el("p", { cls: "empty", text: "No map progress reported yet." }));
    return;
  }
  for (const g of groups) {
    const d = el("details", { cls: "group" });
    d.open = openGroups.has(g.id);
    d.addEventListener("toggle", () => (d.open ? openGroups.add(g.id) : openGroups.delete(g.id)));
    const done = g.total > 0 && g.completed >= g.total;
    const mini = el("div", { cls: `bar mini${done ? " complete" : ""}` }, el("span"));
    (mini.firstElementChild as HTMLElement).style.width = `${percent(g.completed, g.total)}%`;
    const sum = el(
      "summary",
      { attrs: { "data-gid": g.id } },
      el("span", { cls: "name", text: g.label }),
      el("span", { cls: "figs", text: `${fmtInt(g.completed)} / ${fmtInt(g.total)} · ${fmtPercent(g.completed, g.total)}` }),
      mini,
    );
    d.append(sum);
    if (g.levels.length) {
      const body = el("tbody");
      for (const l of g.levels) {
        body.append(
          el("tr", {}, el("th", { text: `Level ${l.level}`, attrs: { scope: "row" } }), el("td", { text: `${fmtInt(l.completed)} / ${fmtInt(l.total)}` }), el("td", { text: fmtPercent(l.completed, l.total) })),
        );
      }
      d.append(
        el(
          "table",
          { cls: "levels" },
          el("caption", { text: `${g.label} levels` }),
          el("thead", {}, el("tr", {}, el("th", { text: "Level", attrs: { scope: "col" } }), el("th", { text: "Done / total", attrs: { scope: "col" } }), el("th", { text: "Share", attrs: { scope: "col" } }))),
          body,
        ),
      );
    }
    refs.groups.append(d);
    if (focused === g.id) sum.focus();
  }
}

function renderData(s: ProjectStatus): void {
  refs.title.textContent = s.job.title;
  document.title = `${s.job.title} · Status`;
  refs.subtitle.textContent = s.job.subtitle;
  refs.stage.textContent = s.stage?.label ?? (s.state === "review" ? "Awaiting review" : "No active stage reported");

  const { completed, total, unit } = s.progress;
  if (completed === null) {
    refs.pct.textContent = "–";
    refs.counts.textContent = `Not yet measured · ${fmtInt(total)} ${unit} expected`;
    refs.bar.removeAttribute("aria-valuenow");
    refs.bar.removeAttribute("aria-valuetext");
    refs.fill.style.width = "0%";
  } else {
    refs.pct.textContent = fmtPercent(completed, total);
    refs.counts.textContent = `${fmtInt(completed)} of ${fmtInt(total)} ${unit}`;
    refs.bar.setAttribute("aria-valuenow", String(Math.round(percent(completed, total))));
    refs.bar.setAttribute("aria-valuetext", `${fmtInt(completed)} of ${fmtInt(total)} ${unit}`);
    refs.fill.style.width = `${percent(completed, total)}%`;
  }
  refs.basis.textContent = [s.progress.measuredAt ? `Progress measured ${fmtTime(s.progress.measuredAt)}` : "Progress not measured yet", `Counts update every ${Math.round(s.progress.censusIntervalSeconds)} seconds`].join(" · ");

  refs.completion.replaceChildren(
    ...Object.entries(s.completion).map(([k, v]) =>
      el("div", {}, el("dt", { text: humanize(k) }), el("dd", { text: v ? "Yes" : "No", cls: v ? "yes" : "" })),
    ),
  );

  renderGroups(s.groups);

  refs.stages.replaceChildren(
    ...s.stages.map((st) =>
      el("li", { cls: st.state, attrs: st.state === "active" ? { "aria-current": "step" } : {} }, el("span", { cls: "label", text: st.label }), el("span", { cls: "st", text: st.state === "complete" ? "Complete" : st.state === "active" ? "In progress" : "Pending" })),
    ),
  );
  if (!s.stages.length) refs.stages.append(el("li", { text: "No pipeline stages reported.", cls: "pending" }));

  const r = s.resources;
  const rows: HTMLElement[] = [];
  if (r.cpuCoresAvailable !== undefined || r.cpuCoresUsed !== undefined) {
    const available = r.cpuCoresAvailable === null || r.cpuCoresAvailable === undefined ? "" : ` / ${r.cpuCoresAvailable.toFixed(0)}`;
    const usage = r.cpuCoresUsed === null || r.cpuCoresUsed === undefined ? "Measuring" : r.cpuCoresUsed.toFixed(2);
    const window = r.cpuSampleSeconds ? ` · ${r.cpuSampleSeconds.toFixed(0)} s average` : "";
    rows.push(kv(`CPU${window}`, `${usage}${available} vCPU`));
  }
  for (const [k, v] of Object.entries(r)) {
    if (["cpuCoresUsed", "cpuCoresAvailable", "cpuSampleSeconds"].includes(k)) continue;
    rows.push(kv(humanize(k), k.endsWith("Bytes") ? fmtBytes(v) : v === null ? "–" : fmtInt(v)));
  }
  refs.resources.replaceChildren(...(rows.length ? rows : [kv("Resources", "Not reported")]));

  const b = s.billing;
  refs.billingSec.hidden = !b;
  if (b) {
    const rowsB = [kv("Gross usage", fmtMoney(b.gross)), kv("Credits applied", fmtMoney(b.credits)), kv("Out of pocket", fmtMoney(b.billed))];
    for (const [k, v] of Object.entries(b.allowances)) rowsB.push(kv(k, fmtMoney(v)));
    refs.billing.replaceChildren(...rowsB);
    refs.billingNote.textContent = `Workspace month to date${b.asOf ? `, as of ${fmtTime(b.asOf)}` : ""}. Delayed meter; not the cost of this job. Remaining credits are unknown.`;
  }

  refs.warnSec.hidden = !s.warnings.length;
  refs.warnings.replaceChildren(...s.warnings.map((w) => el("li", { text: w })));

  const focusedLink = document.activeElement instanceof HTMLAnchorElement ? document.activeElement.dataset.detailKey : undefined;
  const src: HTMLElement[] = [
    kv("Snapshot", fmtTime(s.generatedAt)),
    kv("Source data", fmtTime(s.sourceGeneratedAt)),
    kv("Census", fmtTime(s.censusGeneratedAt)),
    kv("Collector", s.collector.ok ? "Healthy" : "Needs attention", s.collector.ok ? "" : "stale"),
    kv("Last attempt", fmtTime(s.collector.lastAttemptAt)),
  ];
  for (const [index, d] of s.details.entries()) {
    const href = safeHref(d.href);
    const dd = el("dd");
    if (href) dd.append(el("a", { text: d.value ?? d.label, attrs: { href, rel: "noopener noreferrer", "data-detail-key": `${index}:${href}` } }));
    else dd.textContent = d.value ?? "";
    src.push(el("div", {}, el("dt", { text: d.label }), dd));
  }
  refs.source.replaceChildren(...src);
  if (focusedLink) Array.from(refs.source.querySelectorAll<HTMLAnchorElement>('a[data-detail-key]')).find(a => a.dataset.detailKey === focusedLink)?.focus({ preventScroll: true });
}

/** Cheap per-second view: freshness text, stale styling, banner. */
function renderFreshness(): void {
  const s = current;
  if (!s) {
    refs.freshness.textContent = fetchError ? "No data yet" : "Loading…";
    refs.pill.textContent = fetchError ? "Unavailable" : "Loading";
    refs.pill.className = `pill ${fetchError ? "stale" : ""}`;
    setBanner(fetchError, true);
    return;
  }
  const stale = isStale(s);
  const age = fmtAge(ageSeconds(s.sourceGeneratedAt));
  refs.freshness.textContent = `Source ${age} · progress ${fmtAge(ageSeconds(s.censusGeneratedAt))}`;
  const failed = !!fetchError || !s.collector.ok;
  const cls = stale || failed ? "stale" : s.state;
  refs.pill.className = `pill ${cls}`;
  refs.pill.textContent = failed ? "Update issue" : stale ? `${STATE_TEXT[s.state]} · stale` : STATE_TEXT[s.state];
  refs.bar.classList.toggle("stale", stale);
  refs.bar.classList.toggle("complete", !stale && s.progress.completed !== null && s.progress.completed >= s.progress.total);
  const notes: string[] = [];
  if (fetchError) notes.push(`${fetchError} Showing last good data.`);
  if (stale) notes.push(`Source or progress data is stale. Last known values remain visible; see their separate measurement times.`);
  if (!s.collector.ok && s.collector.error) notes.push(`Collector: ${s.collector.error}`);
  setBanner(notes.length ? notes.join(" ") : null, !!fetchError);
}

async function poll(): Promise<void> {
  if (inFlight) return;
  const refreshFocused = document.activeElement === refs.refresh;
  inFlight = true;
  window.clearTimeout(timer);
  refs.refresh.disabled = true;
  const ctl = new AbortController();
  const to = window.setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(STATUS_URL, { cache: "no-store", signal: ctl.signal, headers: { accept: "application/json" } });
    if (!res.ok) throw new Error(`Status request failed (HTTP ${res.status}).`);
    let json: unknown;
    try {
      json = await res.json();
    } catch {
      throw new Error("Status data was not valid JSON.");
    }
    const next = parseStatus(json);
    current = next;
    fetchError = null;
    lastOkAt = Date.now();
    const digest = JSON.stringify(next);
    if (digest !== lastDigest) {
      lastDigest = digest;
      renderData(next);
    }
  } catch (e) {
    fetchError = ctl.signal.aborted ? "Status request timed out." : e instanceof Error ? e.message : "Network error.";
  } finally {
    window.clearTimeout(to);
    inFlight = false;
    refs.refresh.disabled = false;
    if (refreshFocused && document.activeElement === document.body) refs.refresh.focus({ preventScroll: true });
    renderFreshness();
    schedule();
  }
}

function schedule(): void {
  window.clearTimeout(timer);
  if (!document.hidden) timer = window.setTimeout(() => void poll(), POLL_MS);
}

refs.refresh.addEventListener("click", () => void poll());
document.addEventListener("visibilitychange", () => {
  if (document.hidden) window.clearTimeout(timer);
  else void poll();
});
window.setInterval(renderFreshness, 30_000);
renderFreshness();
void poll();
