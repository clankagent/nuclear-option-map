import type { ContourInterval, ContourPyramid } from './catalog';
export interface ContourPath { id: string; elevationMeters: number; isIndex?: boolean; points: Float64Array }
export interface ContourTile { bounds: [number, number, number, number]; paths: ContourPath[] }
/** Fixed screen-distance candidates also work on a single long segment. */
export function contourLabelStations(points: { x: number; y: number }[], spacing = 320, bounds?: [number, number, number, number]) {
  if (!Number.isFinite(spacing) || spacing <= 0) throw new TypeError('Label spacing must be positive and finite');
  if (bounds && (!bounds.every(Number.isFinite) || bounds[0] > bounds[2] || bounds[1] > bounds[3])) throw new TypeError('Invalid label bounds');
  if (points.some(p => !Number.isFinite(p.x) || !Number.isFinite(p.y))) throw new TypeError('Label points must be finite');
  const stations: { x: number; y: number; angle: number }[] = []; let total = 0;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i], length = Math.hypot(b.x - a.x, b.y - a.y);
    if (!Number.isFinite(length) || !Number.isFinite(total + length)) throw new TypeError('Label path length must be finite');
    if (length === 0) continue;
    let enter = 0, exit = 1;
    if (bounds) {
      const dx = b.x - a.x, dy = b.y - a.y;
      const cuts = [[-dx, a.x - bounds[0]], [dx, bounds[2] - a.x], [-dy, a.y - bounds[1]], [dy, bounds[3] - a.y]];
      for (const [direction, distance] of cuts) {
        if (direction === 0) { if (distance < 0) { enter = 1; exit = 0; break; } }
        else if (direction < 0) enter = Math.max(enter, distance / direction);
        else exit = Math.min(exit, distance / direction);
      }
    }
    if (enter <= exit) {
      const first = Math.max(0, Math.ceil((total + enter * length - 180) / spacing));
      const last = Math.floor((total + exit * length - 180) / spacing);
      // Jump directly to visible stations instead of walking millions of offscreen intervals.
      for (let station = first; Number.isFinite(station) && station <= last && stations.length < 4096; station++) {
        const fraction = (180 + station * spacing - total) / length;
        let angle = Math.atan2(b.y - a.y, b.x - a.x); if (angle > Math.PI / 2) angle -= Math.PI; if (angle < -Math.PI / 2) angle += Math.PI;
        stations.push({ x: a.x + (b.x - a.x) * fraction, y: a.y + (b.y - a.y) * fraction, angle });
        if (station + 1 === station) break;
      }
      if (stations.length >= 4096) return stations;
    }
    total += length;
  }
  return stations;
}
export function decodeContours(value: unknown): ContourTile {
  const v = value as { schemaVersion: number; bounds: number[]; paths: { id: string; elevationMeters: number; isIndex?: boolean; points: number[][] }[] };
  if (!v || v.schemaVersion !== 1 || !Array.isArray(v.bounds) || v.bounds.length !== 4 || !v.bounds.every(n => Number.isFinite(n) && Math.abs(n) <= 1e9) || v.bounds[0] >= v.bounds[2] || v.bounds[1] >= v.bounds[3] || !Array.isArray(v.paths) || v.paths.length > 50000) throw new TypeError('Invalid contour tile');
  let vertices = 0;
  const paths = v.paths.map(path => {
    if (!path || typeof path.id !== 'string' || !path.id.length || path.id.length > 256 || !Number.isFinite(path.elevationMeters) || Math.abs(path.elevationMeters) > 1e7 || !Array.isArray(path.points) || path.points.length < 2 || (path.isIndex !== undefined && typeof path.isIndex !== 'boolean')) throw new TypeError('Invalid contour path');
    vertices += path.points.length; if (vertices > 500000) throw new TypeError('Contour tile exceeds vertex limit');
    const points = new Float64Array(path.points.length * 2);
    path.points.forEach((p, i) => { if (!Array.isArray(p) || p.length !== 2 || !p.every(n => Number.isFinite(n) && Math.abs(n) <= 1e9)) throw new TypeError('Invalid contour coordinates'); points[i * 2] = p[0]; points[i * 2 + 1] = p[1]; });
    return { id: path.id, elevationMeters: path.elevationMeters, isIndex: path.isIndex, points };
  });
  return { bounds: v.bounds as ContourTile['bounds'], paths };
}
/** Display-only corner rounding. Measurements and open-path tile endpoints remain unchanged.
 * Each rounded corner stays in the convex hull of its measured segments and
 * within radius/2 of them. Zero preserves the exported contour coordinates.
 */
export function smoothContourPoints(points: Float64Array, radius: number): Float64Array {
  if (!Number.isFinite(radius) || radius < 0 || radius > 160) throw new TypeError('Contour smoothing must be between 0 and 160 meters');
  if (!radius || points.length < 6) return points;
  const closed = points.length >= 8 && points[0] === points[points.length - 2] && points[1] === points[points.length - 1];
  const count = points.length / 2 - (closed ? 1 : 0);
  const result: number[] = closed ? [] : [points[0], points[1]];
  for (let vertex = closed ? 0 : 1; vertex < (closed ? count : count - 1); vertex++) {
    const i = vertex * 2, previous = ((vertex + count - 1) % count) * 2, next = ((vertex + 1) % count) * 2;
    const x = points[i], y = points[i + 1], ax = points[previous] - x, ay = points[previous + 1] - y, bx = points[next] - x, by = points[next + 1] - y;
    const incoming = Math.hypot(ax, ay), outgoing = Math.hypot(bx, by);
    if (!incoming || !outgoing || (ax * bx + ay * by < 0 && Math.abs(ax * by - ay * bx) <= 1e-10 * incoming * outgoing)) { result.push(x, y); continue; }
    const trim = Math.min(radius, incoming / 2, outgoing / 2);
    const sx = x + ax * trim / incoming, sy = y + ay * trim / incoming, ex = x + bx * trim / outgoing, ey = y + by * trim / outgoing;
    result.push(sx, sy);
    for (let step = 1; step <= 6; step++) {
      const t = step / 6, u = 1 - t;
      result.push(u * u * sx + 2 * u * t * x + t * t * ex, u * u * sy + 2 * u * t * y + t * t * ey);
    }
  }
  if (closed) result.push(result[0], result[1]);
  else result.push(points[points.length - 2], points[points.length - 1]);
  return new Float64Array(result);
}
export const CONTOUR_FRAME_PRIMITIVES = 6000;
type DrawBatch = { entry: Entry; regions: [number, number, number, number][] };
type DrawPrimitive = { kind: 'begin'; regions: [number, number, number, number][] } | { kind: 'end' } | { kind: 'label'; text: string; x: number; y: number; angle: number } | { kind: 'line' | 'curve'; index: boolean; ax: number; az: number; bx: number; bz: number; cx?: number; cz?: number } | { kind: 'checkpoint' };
function overlaps(a: ArrayLike<number>, b: ArrayLike<number>) { return a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1]; }
function clipSegment(ax: number, az: number, bx: number, bz: number, bounds: number[]) {
  const dx = bx - ax, dz = bz - az; let enter = 0, exit = 1;
  for (const [direction, distance] of [[-dx, ax - bounds[0]], [dx, bounds[2] - ax], [-dz, az - bounds[1]], [dz, bounds[3] - az]]) {
    if (!direction) { if (distance < 0) return; }
    else if (direction < 0) enter = Math.max(enter, distance / direction); else exit = Math.min(exit, distance / direction);
  }
  if (enter > exit) return;
  return [ax + enter * dx, az + enter * dz, ax + exit * dx, az + exit * dz];
}
/** Screen LOD only: preserve endpoints and keep every removed vertex within tolerance. */
export function reduceContourChunk(points: Float64Array, start: number, end: number, tolerance: number): number[] {
  const keep = new Uint8Array(end - start + 1); keep[0] = keep[keep.length - 1] = 1; const stack = [start, end];
  while (stack.length) {
    const last = stack.pop()!, first = stack.pop()!; if (last - first < 2) continue;
    const ax = points[first * 2], az = points[first * 2 + 1], dx = points[last * 2] - ax, dz = points[last * 2 + 1] - az, norm = dx * dx + dz * dz;
    let maximum = tolerance * tolerance, selected = -1;
    for (let v = first + 1; v < last; v++) {
      const x = points[v * 2] - ax, z = points[v * 2 + 1] - az, f = norm ? Math.max(0, Math.min(1, (x * dx + z * dz) / norm)) : 0, distance = (x - f * dx) ** 2 + (z - f * dz) ** 2;
      if (distance > maximum) { maximum = distance; selected = v; }
    }
    if (selected >= 0) { keep[selected - start] = 1; stack.push(first, selected, selected, last); }
  }
  const selected: number[] = []; for (let i = 0; i < keep.length; i++) if (keep[i]) selected.push(start + i); return selected;
}
interface Entry { tile: ContourTile; ready: number; used: number; bytes: number }
export interface ContourTransform { width: number; height: number; scale: number; dpr?: number; worldToScreen(x: number, z: number): { x: number; y: number }; screenToWorld(x: number, y: number): { x: number; z: number } }
export class ContourRenderer {
  smoothingMeters = 40;
  private indexes = new WeakMap<ContourPath, { bounds: number[]; chunks: Float64Array }>();
  private indexPath(path: ContourPath) { const cached = this.indexes.get(path); if (!cached) throw new Error('Contour index is not ready'); return cached; }
  private *buildIndex(path: ContourPath) {
    const p = path.points, bounds = [Infinity, Infinity, -Infinity, -Infinity], chunks: number[] = [];
    let distance = 0;
    for (let start = 0; start < p.length / 2 - 1; start += 64) {
      const end = Math.min(p.length / 2 - 1, start + 64), box = [Infinity, Infinity, -Infinity, -Infinity];
      for (let v = Math.max(0, start - 1); v <= Math.min(p.length / 2 - 1, end + 1); v++) {
        const x = p[v * 2], z = p[v * 2 + 1]; box[0] = Math.min(box[0], x); box[1] = Math.min(box[1], z); box[2] = Math.max(box[2], x); box[3] = Math.max(box[3], z);
      }
      chunks.push(start, end, ...box, distance);
      for (let v = start; v < end; v++) distance += Math.hypot(p[v * 2 + 2] - p[v * 2], p[v * 2 + 3] - p[v * 2 + 1]);
      if (start && start % 8192 === 0) yield;
      bounds[0] = Math.min(bounds[0], box[0]); bounds[1] = Math.min(bounds[1], box[1]); bounds[2] = Math.max(bounds[2], box[2]); bounds[3] = Math.max(bounds[3], box[3]);
    }
    const value = { bounds, chunks: new Float64Array(chunks) }; this.indexes.set(path, value); return value;
  }
  private revision = 0;
  private work?: { view: string; content: string; iterator: Generator<DrawPrimitive>; transform: ContourTransform; batches: DrawBatch[]; canvas: HTMLCanvasElement; context: CanvasRenderingContext2D; complete: boolean };
  private completed?: { canvas: HTMLCanvasElement; originX: number; originY: number; scale: number; width: number; height: number };
  readonly stats = { lastFramePrimitives: 0, lastFrameMilliseconds: 0, pending: false, completedPrimitives: 0 };
  private cache = new Map<string, Entry>();
  private active = new Map<string, AbortController>();
  private queue: { key: string; z: number; x: number; y: number }[] = [];
  private failed = new Map<string, { attempts: number; retryAt: number }>();
  private retryTimer?: ReturnType<typeof setTimeout>;

  private disposed = false;
  private frame = 0;
  private usage = 0;
  private visible = new Set<string>();
  private lastLevel = 0;
  private outgoingLevel = 0;
  private levelChanged = 0;
  constructor(private pyramid: ContourPyramid, readonly interval: ContourInterval, private worldWidth: number, private worldHeight: number, private redraw: () => void) {}
  private key(z: number, x: number, y: number) { return `${z}/${x}/${y}`; }
  private async load(z: number, x: number, y: number, signal: AbortSignal) {
    const template = this.pyramid.levels[z].urlTemplate;
    const response = await fetch(template.replaceAll('{interval}', String(this.interval)).replaceAll('{z}', String(z)).replaceAll('{x}', String(x)).replaceAll('{y}', String(y)), { signal });
    if (!response.ok) throw new Error(`Contour request failed (${response.status})`);
    const length = Number(response.headers?.get('content-length') ?? 0);
    if (length > 32 * 1024 * 1024) throw new Error('Contour response exceeds size limit');
    const tile = await this.decodeAsync(await response.json(), signal);
    let paths = 0;
    for (const path of tile.paths) {
      const iterator = this.buildIndex(path); while (!iterator.next().done) await this.pause(signal);
      if (++paths % 128 === 0) await this.pause(signal);
    }
    const l = this.pyramid.levels[z], expected = [-this.worldWidth / 2 + x / l.columns * this.worldWidth, this.worldHeight / 2 - (y + 1) / l.rows * this.worldHeight, -this.worldWidth / 2 + (x + 1) / l.columns * this.worldWidth, this.worldHeight / 2 - y / l.rows * this.worldHeight];
    if (tile.bounds.some((n, i) => Math.abs(n - expected[i]) > .5)) throw new Error('Contour bounds do not match tile ownership');
    return tile;
  }
  private async pause(signal: AbortSignal) {
    await new Promise<void>(resolve => setTimeout(resolve, 0));
    if (signal.aborted) throw new DOMException('Contour request canceled', 'AbortError');
  }
  private async decodeAsync(value: unknown, signal: AbortSignal): Promise<ContourTile> {
    const v = value as { schemaVersion: number; bounds: number[]; paths: { id: string; elevationMeters: number; isIndex?: boolean; points: number[][] }[] };
    const header = decodeContours({ ...v, paths: [] });
    if (!Array.isArray(v.paths) || v.paths.length > 50000) throw new TypeError('Invalid contour tile');
    let vertices = 0, copied = 0; const paths: ContourPath[] = [];
    for (const path of v.paths) {
      if (!path || typeof path.id !== 'string' || !path.id.length || path.id.length > 256 || !Number.isFinite(path.elevationMeters) || Math.abs(path.elevationMeters) > 1e7 || !Array.isArray(path.points) || path.points.length < 2 || (path.isIndex !== undefined && typeof path.isIndex !== 'boolean')) throw new TypeError('Invalid contour path');
      vertices += path.points.length; if (vertices > 500000) throw new TypeError('Contour tile exceeds vertex limit');
      const points = new Float64Array(path.points.length * 2);
      for (let i = 0; i < path.points.length; i++) {
        const p = path.points[i]; if (!Array.isArray(p) || p.length !== 2 || !p.every(n => Number.isFinite(n) && Math.abs(n) <= 1e9)) throw new TypeError('Invalid contour coordinates');
        points[i * 2] = p[0]; points[i * 2 + 1] = p[1]; if (++copied % 2048 === 0) await this.pause(signal);
      }
      paths.push({ id: path.id, elevationMeters: path.elevationMeters, isIndex: path.isIndex, points });
    }
    return { bounds: header.bounds, paths };
  }
  async prepare() {
    try { await Promise.all(Array.from({ length: this.pyramid.levels[0].rows }, (_, y) => Array.from({ length: this.pyramid.levels[0].columns }, async (_, x) => {
      const key = this.key(0, x, y), controller = new AbortController(); this.active.set(key, controller);
      try { const tile = await this.load(0, x, y, controller.signal); if (!this.disposed) this.store(key, tile); }
      finally { this.active.delete(key); }
    })).flat()); } catch (error) { this.destroy(); throw error; }
  }
  private store(key: string, tile: ContourTile) { this.revision++; this.failed.delete(key); this.cache.set(key, { tile, ready: performance.now(), used: ++this.usage, bytes: 256 + tile.paths.reduce((sum, p) => sum + p.points.byteLength * 1.5 + 128 + p.id.length * 2, 0) }); }
  private scheduleRetry() {
    if (this.retryTimer !== undefined) clearTimeout(this.retryTimer); this.retryTimer = undefined;
    const pending = [...this.failed].filter(([key, f]) => this.visible.has(key) && f.attempts < 3).map(([, f]) => f.retryAt);
    if (pending.length) this.retryTimer = setTimeout(() => { this.retryTimer = undefined; if (!this.disposed) this.redraw(); }, Math.max(1, Math.min(...pending) - performance.now()));
  }
  private pump() {
    while (!this.disposed && this.active.size < 4 && this.queue.length) {
      const r = this.queue.shift()!, controller = new AbortController(); this.active.set(r.key, controller);
      void this.load(r.z, r.x, r.y, controller.signal).then(tile => { if (!this.disposed && !controller.signal.aborted) { this.store(r.key, tile); this.redraw(); } }).catch(() => { if (!this.disposed && !controller.signal.aborted) { const attempts = (this.failed.get(r.key)?.attempts ?? 0) + 1; this.failed.set(r.key, { attempts, retryAt: performance.now() + 500 * 2 ** (attempts - 1) }); this.scheduleRetry(); } }).finally(() => { if (this.active.get(r.key) === controller) this.active.delete(r.key); this.pump(); });
    }
  }
  draw(destination: CanvasRenderingContext2D, transform: ContourTransform) {
    if (this.disposed || !this.cache.size) return;
    const started = performance.now(), level = Math.min(this.pyramid.levels.length - 1, Math.max(0, Math.ceil(Math.log2(Math.max(this.worldWidth, this.worldHeight) * transform.scale / 1024))));
    const { columns, rows } = this.pyramid.levels[level], nw = transform.screenToWorld(0, 0), se = transform.screenToWorld(transform.width, transform.height);
    const minX = Math.max(0, Math.floor((nw.x + this.worldWidth / 2) / this.worldWidth * columns)), maxX = Math.min(columns - 1, Math.floor((se.x + this.worldWidth / 2) / this.worldWidth * columns));
    const minY = Math.max(0, Math.floor((this.worldHeight / 2 - nw.z) / this.worldHeight * rows)), maxY = Math.min(rows - 1, Math.floor((this.worldHeight / 2 - se.z) / this.worldHeight * rows));
    const groups = new Map<Entry, DrawBatch>(); this.queue = []; this.visible.clear();
    for (let y = minY; y <= maxY; y++) for (let x = minX; x <= maxX; x++) {
      const key = this.key(level, x, y); this.visible.add(key); const failure = this.failed.get(key);
      if (!this.cache.has(key) && !this.active.has(key) && (!failure || failure.attempts < 3 && failure.retryAt <= performance.now())) this.queue.push({ key, z: level, x, y });
      let entry: Entry | undefined;
      for (let z = level; z >= 0; z--) { const k = this.key(z, Math.floor(x / 2 ** (level - z)), Math.floor(y / 2 ** (level - z))); this.visible.add(k); const e = this.cache.get(k); if (e) { entry = e; break; } }
      if (!entry) continue; entry.used = ++this.usage;
      const a = transform.worldToScreen(-this.worldWidth / 2 + x / columns * this.worldWidth, this.worldHeight / 2 - y / rows * this.worldHeight), b = transform.worldToScreen(-this.worldWidth / 2 + (x + 1) / columns * this.worldWidth, this.worldHeight / 2 - (y + 1) / rows * this.worldHeight);
      let batch = groups.get(entry); if (!batch) { batch = { entry, regions: [] }; groups.set(entry, batch); } batch.regions.push([a.x, a.y, b.x, b.y]);
    }
    for (const [key, controller] of this.active) if (!key.startsWith('0/') && !this.visible.has(key)) { controller.abort(); this.active.delete(key); }
    this.pump(); this.evict(); this.scheduleRetry();
    const origin = transform.worldToScreen(0, 0), ratio = Math.min(transform.dpr ?? 1, 4096 / Math.max(transform.width, transform.height));
    const view = [transform.width, transform.height, transform.scale, origin.x, origin.y, ratio, this.smoothingMeters].join(':');
    const content = `${view}:${this.revision}`;
    // Finish a bounded snapshot while tiles arrive. View changes cancel unfinished
    // work; the last completed layer remains available and is reprojected below.
    if (!this.work || this.work.view !== view || this.work.complete && this.work.content !== content) {
      const canvas = this.work && this.work.canvas !== this.completed?.canvas ? this.work.canvas : document.createElement('canvas'); canvas.width = Math.max(1, Math.ceil(transform.width * ratio)); canvas.height = Math.max(1, Math.ceil(transform.height * ratio));
      const context = canvas.getContext('2d'); if (!context) throw new Error('Unable to allocate contour drawing buffer');
      context.setTransform(ratio, 0, 0, ratio, 0, 0); context.clearRect(0, 0, transform.width, transform.height);
      const batches = [...groups.values()];
      this.work = { view, content, transform, batches, canvas, context, complete: false, iterator: this.primitives(batches, transform, context) };
    }
    const work = this.work; let count = 0, checkpoints = 0, strokeSize = 0, strokeIndex: boolean | undefined;
    const flushStroke = () => { if (strokeSize) { work.context.stroke(); strokeSize = 0; } };
    const o = transform.worldToScreen(0, 0), ux = transform.worldToScreen(1, 0), uz = transform.worldToScreen(0, 1);
    const screen = (x: number, z: number) => [o.x + x * (ux.x - o.x) + z * (uz.x - o.x), o.y + x * (ux.y - o.y) + z * (uz.y - o.y)];
    if (!work.complete) {
      while (count < CONTOUR_FRAME_PRIMITIVES && checkpoints < 256) {
        if ((count + checkpoints) % 32 === 0 && performance.now() - started >= 6 && count + checkpoints) break;
        const next = work.iterator.next();
        if (next.done) {
          flushStroke(); work.complete = true;
          if (this.completed && this.completed.canvas !== work.canvas) { this.completed.canvas.width = 0; this.completed.canvas.height = 0; }
          this.completed = { canvas: work.canvas, originX: origin.x, originY: origin.y, scale: transform.scale, width: transform.width, height: transform.height }; break;
        }
        const p = next.value; if (p.kind === 'checkpoint') { checkpoints++; continue; }
        if (p.kind === 'begin') { flushStroke(); const ctx = work.context; ctx.save(); ctx.beginPath(); for (const r of p.regions) ctx.rect(r[0], r[1], r[2] - r[0], r[3] - r[1]); ctx.clip(); ctx.strokeStyle = '#967d61'; ctx.lineJoin = 'round'; ctx.lineCap = 'round'; continue; }
        if (p.kind === 'end') { flushStroke(); work.context.restore(); continue; }
        if (p.kind === 'label') { const ctx = work.context; ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.angle); ctx.strokeText(p.text, 0, 0); ctx.fillText(p.text, 0, 0); ctx.restore(); count++; continue; }
        const ctx = work.context, a = screen(p.ax, p.az), b = screen(p.bx, p.bz);
        if (strokeSize && (strokeIndex !== p.index || strokeSize >= 256)) flushStroke();
        if (!strokeSize) { ctx.lineWidth = p.index ? 1.1 : .55; ctx.beginPath(); strokeIndex = p.index; }
        ctx.moveTo(a[0], a[1]);
        if (p.kind === 'curve') { const c = screen(p.cx!, p.cz!); ctx.quadraticCurveTo(c[0], c[1], b[0], b[1]); } else ctx.lineTo(b[0], b[1]);
        strokeSize++; count++; this.stats.completedPrimitives++;
      }
    }
    flushStroke();
    this.stats.lastFramePrimitives = count; this.stats.lastFrameMilliseconds = performance.now() - started; this.stats.pending = !work.complete || work.content !== content;
    if (this.completed) {
      const c = this.completed, factor = transform.scale / c.scale;
      destination.drawImage(c.canvas, 0, 0, c.canvas.width, c.canvas.height, origin.x - c.originX * factor, origin.y - c.originY * factor, c.width * factor, c.height * factor);
    } else destination.drawImage(work.canvas, 0, 0, work.canvas.width, work.canvas.height, 0, 0, transform.width, transform.height);
    if (this.stats.pending && !this.frame) this.frame = requestAnimationFrame(() => { this.frame = 0; if (!this.disposed) this.redraw(); });
  }
  private index(p: ContourPath) { return p.isIndex ?? Math.abs(p.elevationMeters / (this.interval * 5) - Math.round(p.elevationMeters / (this.interval * 5))) < 1e-6; }
  private corner(points: Float64Array, v: number, radius: number) {
    const count = points.length / 2, closed = count >= 4 && points[0] === points[points.length - 2] && points[1] === points[points.length - 1], unique = count - (closed ? 1 : 0);
    if (closed && v === unique) v = 0;
    const x = points[v * 2], z = points[v * 2 + 1];
    if (!radius || !closed && (v === 0 || v === count - 1)) return [x, z, x, z];
    const prev = (v + unique - 1) % unique, next = (v + 1) % unique, ax = points[prev * 2] - x, az = points[prev * 2 + 1] - z, bx = points[next * 2] - x, bz = points[next * 2 + 1] - z;
    const incoming = Math.hypot(ax, az), outgoing = Math.hypot(bx, bz);
    if (!incoming || !outgoing || ax * bx + az * bz < 0 && Math.abs(ax * bz - az * bx) <= 1e-10 * incoming * outgoing) return [x, z, x, z];
    const trim = Math.min(radius, incoming / 2, outgoing / 2); return [x + ax * trim / incoming, z + az * trim / incoming, x + bx * trim / outgoing, z + bz * trim / outgoing];
  }
  private *primitives(batches: DrawBatch[], t: ContourTransform, ctx: CanvasRenderingContext2D): Generator<DrawPrimitive> {
    const nw = t.screenToWorld(-2, -2), se = t.screenToWorld(t.width + 2, t.height + 2), visible = [nw.x, se.z, se.x, nw.z];
    const radius = this.smoothingMeters * t.scale < .5 ? 0 : this.smoothingMeters;
    let scans = 0;
    for (const batch of batches) {
      yield { kind: 'begin', regions: batch.regions };
      const covered = batch.regions.map(r => { const a = t.screenToWorld(r[0], r[1]), b = t.screenToWorld(r[2], r[3]); return [Math.max(visible[0], a.x - 2 / t.scale), Math.max(visible[1], b.z - 2 / t.scale), Math.min(visible[2], b.x + 2 / t.scale), Math.min(visible[3], a.z + 2 / t.scale)]; });
      for (const path of batch.entry.tile.paths) {
        if (++scans % 128 === 0) yield { kind: 'checkpoint' };
        const indexed = this.indexPath(path); if (!overlaps(indexed.bounds, visible)) continue;
        const p = path.points, chunks = indexed.chunks, index = this.index(path);
        for (let c = 0; c < chunks.length; c += 7) {
          if (++scans % 128 === 0) yield { kind: 'checkpoint' };
          if (!covered.some(box => overlaps(chunks.subarray(c + 2, c + 6), box))) continue;
          const reduced = radius ? undefined : reduceContourChunk(p, chunks[c], chunks[c + 1], .25 / t.scale);
          const count = reduced ? reduced.length - 1 : chunks[c + 1] - chunks[c];
          for (let n = 0; n < count; n++) {
            const v = reduced ? reduced[n] : chunks[c] + n, next = reduced ? reduced[n + 1] : v + 1;
            const a = this.corner(p, v, radius), b = this.corner(p, next, radius);
            // Emit once even when a parent owns adjacent regions. The union clip
            // supplies exact region ownership without overlapping stroke coverage.
            if (covered.some(region => clipSegment(a[2], a[3], b[0], b[1], region))) {
              const clipped = clipSegment(a[2], a[3], b[0], b[1], visible);
              if (clipped) yield { kind: 'line', index, ax: clipped[0], az: clipped[1], bx: clipped[2], bz: clipped[3] };
            }
            if (b[0] !== b[2] || b[1] !== b[3]) {
              const cx = p[next * 2], cz = p[next * 2 + 1];
              const box = [Math.min(b[0], b[2], cx), Math.min(b[1], b[3], cz), Math.max(b[0], b[2], cx), Math.max(b[1], b[3], cz)];
              if (covered.some(r => overlaps(box, r))) yield { kind: 'curve', index, ax: b[0], az: b[1], bx: b[2], bz: b[3], cx, cz };
            }
          }
        }
      }
      yield { kind: 'end' };
    }
    yield* this.labels(ctx, batches, t);
  }
  private *labels(ctx: CanvasRenderingContext2D, batches: DrawBatch[], t: ContourTransform): Generator<DrawPrimitive> {
    const occupied: number[][] = [], seen = new Set<ContourPath>(), widths = new Map<string, number>(); let attempts = 0, scanned = 0;
    const origin = t.worldToScreen(0, 0);
    const nw = t.screenToWorld(0, 0), se = t.screenToWorld(t.width, t.height), visible = [nw.x, se.z, se.x, nw.z];
    ctx.save(); ctx.font = '10px system-ui'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = '#80684f'; ctx.strokeStyle = '#f6f4ed'; ctx.lineWidth = 3;
    outer: for (const batch of batches) for (const path of batch.entry.tile.paths) {
      if (++scanned % 128 === 0) yield { kind: 'checkpoint' };
      if (!this.index(path) || seen.has(path)) continue; seen.add(path); const indexed = this.indexPath(path); if (!overlaps(indexed.bounds, visible)) continue;
      const text = `${path.elevationMeters} m`, p = path.points; let placed = 0;
      const chunks = indexed.chunks;
      for (let c = 0; c < chunks.length; c += 7) {
        if (++scanned % 128 === 0) yield { kind: 'checkpoint' };
        if (!overlaps(chunks.subarray(c + 2, c + 6), visible)) continue;
        let total = chunks[c + 6] * t.scale;
        for (let v = chunks[c]; v < chunks[c + 1]; v++) {
          const ax = p[v * 2], az = p[v * 2 + 1], bx = p[v * 2 + 2], bz = p[v * 2 + 3], length = Math.hypot(bx - ax, bz - az) * t.scale;
          const clipped = clipSegment(ax, az, bx, bz, visible);
          if (clipped && length) {
            const enter = Math.hypot(clipped[0] - ax, clipped[1] - az) * t.scale, exit = Math.hypot(clipped[2] - ax, clipped[3] - az) * t.scale;
            for (let station = Math.max(0, Math.ceil((total + enter - 180) / 320)); 180 + station * 320 <= total + exit; station++) {
              if (++attempts > 512 || occupied.length >= 64) break outer;
              const f = (180 + station * 320 - total) / length, x = origin.x + (ax + (bx - ax) * f) * t.scale, y = origin.y - (az + (bz - az) * f) * t.scale;
              let textWidth = widths.get(text); if (textWidth === undefined) { textWidth = ctx.measureText(text).width; widths.set(text, textWidth); }
              let angle = Math.atan2(-(bz - az), bx - ax); if (angle > Math.PI / 2) angle -= Math.PI; if (angle < -Math.PI / 2) angle += Math.PI;
              const rx = Math.abs(Math.cos(angle)) * (textWidth / 2 + 8) + Math.abs(Math.sin(angle)) * 10, ry = Math.abs(Math.sin(angle)) * (textWidth / 2 + 8) + Math.abs(Math.cos(angle)) * 10, box = [x - rx, y - ry, x + rx, y + ry];
              if (box[0] < 16 || box[1] < 16 || box[2] > t.width - 16 || box[3] > t.height - 16 || !batch.regions.some(r => box[0] >= r[0] && box[1] >= r[1] && box[2] <= r[2] && box[3] <= r[3]) || occupied.some(o => overlaps(o, box))) continue;
              occupied.push(box); yield { kind: 'label', text, x, y, angle }; if (++placed >= 3) break;
            }
          }
          total += length; if (placed >= 3) break;
        }
        if (placed >= 3) break;
      }
    }
    ctx.restore();
  }
  private evict() {
    let bytes = [...this.cache.values()].reduce((n, e) => n + e.bytes, 0);
    for (const [key, entry] of [...this.cache].sort((a, b) => a[1].used - b[1].used)) { if (bytes <= 32 * 1024 * 1024) break; if (key.startsWith('0/') || this.visible.has(key)) continue; this.cache.delete(key); bytes -= entry.bytes; }
  }
  destroy() { this.disposed = true; if (this.frame) cancelAnimationFrame(this.frame); if (this.retryTimer !== undefined) clearTimeout(this.retryTimer); for (const controller of this.active.values()) controller.abort(); this.active.clear(); this.cache.clear(); this.queue = []; this.failed.clear(); if (this.work) { this.work.canvas.width = 0; this.work.canvas.height = 0; this.work = undefined; } if (this.completed) { this.completed.canvas.width = 0; this.completed.canvas.height = 0; this.completed = undefined; } }
}
