import { fmtInt, fmtTime, type CoverageMap, type CoverageState } from "./model";

export const ZOOM_STEPS = [1, 2, 3, 4, 6, 8] as const;
export const MIN_ZOOM = 1;
export const MAX_ZOOM = 8;

export const MEASURE_NOTE =
  "Each map pixel is one 128 m by 128 m world tile, which is a 512-pixel render core at 0.25 m per pixel. It is not one pixel of the actual imagery. Green means output was collected; it does not mean the dataset is accepted.";

export interface Cell {
  col: number;
  row: number;
}

export const clampZoom = (z: number): number => (Number.isFinite(z) ? Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z)) : MIN_ZOOM);

/** Next preset zoom in the given direction; stays put at either end. */
export function stepZoom(zoom: number, dir: 1 | -1): number {
  if (dir > 0) return ZOOM_STEPS.find((s) => s > zoom + 1e-9) ?? MAX_ZOOM;
  return [...ZOOM_STEPS].reverse().find((s) => s < zoom - 1e-9) ?? MIN_ZOOM;
}

/** Pointer position within the displayed canvas to a map tile, or null when outside. */
export function cellAt(x: number, y: number, cssWidth: number, cssHeight: number, map: { width: number; height: number }): Cell | null {
  if (!(cssWidth > 0) || !(cssHeight > 0) || x < 0 || y < 0 || x >= cssWidth || y >= cssHeight) return null;
  return { col: Math.min(map.width - 1, Math.floor((x / cssWidth) * map.width)), row: Math.min(map.height - 1, Math.floor((y / cssHeight) * map.height)) };
}

export function moveCell(cell: Cell | null, key: string, map: { width: number; height: number }, big = false): Cell | null {
  const step = big ? 10 : 1;
  const from = cell ?? { col: Math.floor(map.width / 2), row: Math.floor(map.height / 2) };
  const d: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
  const delta = d[key];
  if (key === "Home") return { col: 0, row: 0 };
  if (key === "End") return { col: map.width - 1, row: map.height - 1 };
  if (!delta) return null;
  if (!cell) return from;
  return { col: Math.min(map.width - 1, Math.max(0, from.col + delta[0])), row: Math.min(map.height - 1, Math.max(0, from.row + delta[1])) };
}

export type KeyAction = "zoom-in" | "zoom-out" | "reset" | "fit" | "move" | null;
export function keyAction(key: string): KeyAction {
  if (key === "+" || key === "=") return "zoom-in";
  if (key === "-" || key === "_") return "zoom-out";
  if (key === "0") return "reset";
  if (key === "f" || key === "F") return "fit";
  if (key.startsWith("Arrow") || key === "Home" || key === "End") return "move";
  return null;
}

const hex = (c: string): [number, number, number] => [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)];

/** Exact RGB match to a state's palette color; the PNG is the only source of per-tile state. */
export function matchState(r: number, g: number, b: number, a: number, states: CoverageState[]): CoverageState | null {
  if (a !== 255) return null;
  return states.find((s) => {
    const [sr, sg, sb] = hex(s.color);
    return sr === r && sg === g && sb === b;
  }) ?? null;
}

export function describeCell(map: CoverageMap, cell: Cell, state: CoverageState | null): string {
  const m = map.cellMeters;
  const east = fmtInt(cell.col * m);
  const south = fmtInt(cell.row * m);
  return `Tile column ${cell.col}, row ${cell.row} · ${east} m east and ${south} m south of the top-left corner · ${state ? state.label : "state unknown"}`;
}

export function describeMap(map: CoverageMap): string {
  const parts = map.states.map((s) => `${s.label} ${fmtInt(s.count)}`);
  return `${map.label} coverage map, ${map.width} by ${map.height} tiles of ${map.cellMeters} meters. ${parts.join(", ")}.`;
}

export interface CoverageRefs {
  section: HTMLElement;
  tabs: HTMLElement;
  zoomOut: HTMLButtonElement;
  zoomIn: HTMLButtonElement;
  fit: HTMLButtonElement;
  reset: HTMLButtonElement;
  zoomLabel: HTMLElement;
  view: HTMLElement;
  stage: HTMLElement;
  canvas: HTMLCanvasElement;
  cursor: HTMLElement;
  readout: HTMLElement;
  description: HTMLElement;
  legend: HTMLElement;
  notice: HTMLElement;
  measure: HTMLElement;
}

interface Slot {
  map: CoverageMap;
  /** Image string actually drawn on the shared canvas for this map, if any. */
  drawn: string | null;
  failed: boolean;
  token: number;
  bitmap: HTMLCanvasElement | null;
}

export interface CoverageController {
  update(jobId: string, maps: CoverageMap[]): void;
  setStale(stale: boolean): void;
}

export function createCoverage(r: CoverageRefs): CoverageController {
  const slots = new Map<string, Slot>();
  let jobId: string | null = null;
  let selected: string | null = null;
  let zoom = 1;
  let cell: Cell | null = null;
  let stale = false;
  let tabKey = "";
  let canvasFor: string | null = null;
  let resizer: ResizeObserver | undefined;

  r.measure.textContent = MEASURE_NOTE;
  const ctx = (): CanvasRenderingContext2D | null => r.canvas.getContext("2d", { willReadFrequently: true });
  const current = (): Slot | undefined => (selected ? slots.get(selected) : undefined);

  function layout(keepCenter = true): void {
    const slot = current();
    if (!slot) return;
    const { view, stage, canvas } = r;
    const fx = keepCenter && stage.offsetWidth ? (view.scrollLeft + view.clientWidth / 2) / stage.offsetWidth : 0;
    const fy = keepCenter && stage.offsetHeight ? (view.scrollTop + view.clientHeight / 2) / stage.offsetHeight : 0;
    const fit = Math.min(Math.max(1, view.clientWidth) / slot.map.width, Math.max(1, Math.floor(Math.min(window.innerHeight * 0.7, 640)) - 2) / slot.map.height);
    const width = Math.round(slot.map.width * fit * zoom);
    const height = Math.round(slot.map.height * fit * zoom);
    stage.style.width = `${width}px`;
    stage.style.height = `${height}px`;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    if (keepCenter) {
      view.scrollLeft = Math.max(0, fx * width - view.clientWidth / 2);
      view.scrollTop = Math.max(0, fy * height - view.clientHeight / 2);
    }
    r.zoomLabel.textContent = `${zoom}×`;
    r.zoomOut.disabled = zoom <= MIN_ZOOM;
    r.zoomIn.disabled = zoom >= MAX_ZOOM;
    placeCursor();
  }

  function placeCursor(): void {
    const slot = current();
    if (!slot || !cell) {
      r.cursor.hidden = true;
      return;
    }
    const w = r.canvas.offsetWidth / slot.map.width;
    const h = r.canvas.offsetHeight / slot.map.height;
    r.cursor.hidden = false;
    r.cursor.style.left = `${cell.col * w}px`;
    r.cursor.style.top = `${cell.row * h}px`;
    r.cursor.style.width = `${Math.max(w, 6)}px`;
    r.cursor.style.height = `${Math.max(h, 6)}px`;
  }

  function readCell(): void {
    const slot = current();
    if (!slot || !cell) {
      r.readout.textContent = "Hover, tap or use the arrow keys on the map to read a tile.";
      return;
    }
    const c = ctx();
    let state: CoverageState | null = null;
    if (c && slot.drawn && canvasFor === slot.map.id) {
      const d = c.getImageData(cell.col, cell.row, 1, 1).data;
      state = matchState(d[0]!, d[1]!, d[2]!, d[3]!, slot.map.states);
    }
    r.readout.textContent = describeCell(slot.map, cell, state);
  }

  function setZoom(next: number, resetScroll = false): void {
    zoom = clampZoom(next);
    layout(!resetScroll);
    if (resetScroll) {
      r.view.scrollLeft = 0;
      r.view.scrollTop = 0;
    }
  }

  function drawSlot(slot: Slot): void {
    const c = ctx();
    if (!c || !slot.bitmap) return;
    r.canvas.width = slot.map.width;
    r.canvas.height = slot.map.height;
    c.imageSmoothingEnabled = false;
    c.drawImage(slot.bitmap, 0, 0);
    canvasFor = slot.map.id;
    layout();
    readCell();
  }

  /** Decode off-screen first so a bad new image never replaces the last good one. */
  function load(slot: Slot): void {
    const token = ++slot.token;
    const img = new Image();
    img.onload = () => {
      if (token !== slot.token || slots.get(slot.map.id) !== slot) return;
      const buf = document.createElement("canvas");
      buf.width = slot.map.width;
      buf.height = slot.map.height;
      const bc = buf.getContext("2d");
      if (!bc || img.naturalWidth !== slot.map.width || img.naturalHeight !== slot.map.height) return fail(slot, token);
      bc.drawImage(img, 0, 0);
      slot.bitmap = buf;
      slot.drawn = slot.map.image;
      slot.failed = false;
      if (selected === slot.map.id) drawSlot(slot);
      notice();
    };
    img.onerror = () => fail(slot, token);
    img.src = slot.map.image;
  }

  function fail(slot: Slot, token: number): void {
    if (token !== slot.token) return;
    slot.failed = true;
    notice();
  }

  function notice(): void {
    const slot = current();
    const msgs: string[] = [];
    if (slot?.failed) msgs.push(slot.drawn ? "The newest map image could not be decoded. Showing the last good image." : "The map image could not be decoded. Counts below are still current.");
    if (stale) msgs.push("Source or census data is stale. Colors show the last collected state.");
    r.notice.textContent = msgs.join(" ");
    r.notice.hidden = msgs.length === 0;
  }

  function renderTabs(maps: CoverageMap[]): void {
    const key = maps.map((m) => `${m.id}\u0000${m.label}`).join("\u0001");
    if (key !== tabKey) {
      tabKey = key;
      const focused = document.activeElement instanceof HTMLElement ? document.activeElement.dataset.mapId : undefined;
      r.tabs.replaceChildren(
        ...maps.map((m) => {
          const b = document.createElement("button");
          b.type = "button";
          b.textContent = m.label;
          b.dataset.mapId = m.id;
          b.addEventListener("click", () => select(m.id));
          return b;
        }),
      );
      if (focused) r.tabs.querySelector<HTMLElement>(`[data-map-id="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
    }
    for (const b of Array.from(r.tabs.querySelectorAll<HTMLButtonElement>("button"))) b.setAttribute("aria-pressed", String(b.dataset.mapId === selected));
    r.tabs.hidden = maps.length < 2;
  }

  function renderInfo(): void {
    const slot = current();
    if (!slot) return;
    const m = slot.map;
    r.view.setAttribute("aria-label", `${m.label} coverage map. Arrow keys read tiles, plus and minus zoom, 0 resets.`);
    const desc = `${describeMap(m)}${m.measuredAt ? ` Measured ${fmtTime(m.measuredAt)}.` : ""}`;
    if (r.description.textContent !== desc) r.description.textContent = desc;
    r.legend.replaceChildren(
      ...m.states.map((s) => {
        const sw = document.createElement("span");
        sw.className = "swatch";
        sw.style.background = s.color;
        sw.setAttribute("aria-hidden", "true");
        const li = document.createElement("li");
        const name = document.createElement("span");
        name.textContent = s.label;
        const n = document.createElement("span");
        n.className = "n";
        n.textContent = fmtInt(s.count);
        li.append(sw, name, n);
        return li;
      }),
    );
  }

  function select(id: string): void {
    if (!slots.has(id) || id === selected) return;
    selected = id;
    cell = null;
    zoom = 1;
    const slot = current()!;
    renderTabs(Array.from(slots.values()).map((s) => s.map));
    renderInfo();
    if (slot.bitmap) drawSlot(slot);
    else {
      canvasFor = null;
      r.canvas.getContext("2d")?.clearRect(0, 0, r.canvas.width, r.canvas.height);
      layout(false);
    }
    r.view.scrollLeft = 0;
    r.view.scrollTop = 0;
    readCell();
    notice();
  }

  function point(e: { clientX: number; clientY: number }): void {
    const slot = current();
    if (!slot) return;
    const box = r.canvas.getBoundingClientRect();
    const next = cellAt(e.clientX - box.left, e.clientY - box.top, box.width, box.height, slot.map);
    if (!next) return;
    cell = next;
    placeCursor();
    readCell();
  }

  r.canvas.addEventListener("pointermove", point);
  r.canvas.addEventListener("pointerdown", point);
  r.canvas.addEventListener("pointerleave", () => {
    if (document.activeElement !== r.view) {
      cell = null;
      placeCursor();
      readCell();
    }
  });
  r.zoomIn.addEventListener("click", () => setZoom(stepZoom(zoom, 1)));
  r.zoomOut.addEventListener("click", () => setZoom(stepZoom(zoom, -1)));
  r.fit.addEventListener("click", () => setZoom(1, true));
  r.reset.addEventListener("click", () => {
    cell = null;
    setZoom(1, true);
    placeCursor();
    readCell();
  });
  r.view.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const a = keyAction(e.key);
    const slot = current();
    if (!a || !slot) return;
    e.preventDefault();
    if (a === "zoom-in") setZoom(stepZoom(zoom, 1));
    else if (a === "zoom-out") setZoom(stepZoom(zoom, -1));
    else if (a === "reset") r.reset.click();
    else if (a === "fit") setZoom(1, true);
    else {
      const next = moveCell(cell, e.key, slot.map, e.shiftKey);
      if (!next) return;
      cell = next;
      placeCursor();
      readCell();
      const w = r.canvas.offsetWidth / slot.map.width;
      const h = r.canvas.offsetHeight / slot.map.height;
      const left = cell.col * w;
      const top = cell.row * h;
      if (left < r.view.scrollLeft || left + w > r.view.scrollLeft + r.view.clientWidth) r.view.scrollLeft = left - r.view.clientWidth / 2;
      if (top < r.view.scrollTop || top + h > r.view.scrollTop + r.view.clientHeight) r.view.scrollTop = top - r.view.clientHeight / 2;
    }
  });
  if (typeof ResizeObserver !== "undefined") {
    resizer = new ResizeObserver(() => layout());
    resizer.observe(r.view);
  }

  return {
    update(nextJob, maps) {
      if (jobId !== null && jobId !== nextJob) {
        for (const s of slots.values()) s.token++;
        slots.clear();
        selected = null;
        cell = null;
        zoom = 1;
        canvasFor = null;
        tabKey = "";
        r.canvas.getContext("2d")?.clearRect(0, 0, r.canvas.width, r.canvas.height);
      }
      jobId = nextJob;
      r.section.hidden = maps.length === 0;
      if (!maps.length) {
        slots.clear();
        selected = null;
        return;
      }
      for (const id of Array.from(slots.keys())) if (!maps.some((m) => m.id === id)) slots.delete(id);
      for (const m of maps) {
        const slot = slots.get(m.id);
        if (!slot) {
          const fresh: Slot = { map: m, drawn: null, failed: false, token: 0, bitmap: null };
          slots.set(m.id, fresh);
          load(fresh);
        } else {
          const changed = slot.map.image !== m.image;
          slot.map = m;
          if (changed && slot.drawn !== m.image) load(slot);
          else if (changed) slot.failed = false;
        }
      }
      if (!selected || !slots.has(selected)) {
        selected = null;
        select(maps[0]!.id);
      } else {
        renderTabs(maps);
        renderInfo();
        notice();
      }
    },
    setStale(next) {
      if (stale === next) return;
      stale = next;
      notice();
    },
  };
}
