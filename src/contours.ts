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
interface Entry { tile: ContourTile; ready: number; used: number; bytes: number }
export interface ContourTransform { width: number; height: number; scale: number; dpr?: number; worldToScreen(x: number, z: number): { x: number; y: number }; screenToWorld(x: number, y: number): { x: number; z: number } }
export class ContourRenderer {
  private cache = new Map<string, Entry>();
  private active = new Map<string, AbortController>();
  private queue: { key: string; z: number; x: number; y: number }[] = [];
  private failed = new Map<string, { attempts: number; retryAt: number }>();
  private retryTimer?: ReturnType<typeof setTimeout>;
  private buffer?: HTMLCanvasElement;
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
    const tile = decodeContours(await response.json());
    const l = this.pyramid.levels[z], expected = [-this.worldWidth / 2 + x / l.columns * this.worldWidth, this.worldHeight / 2 - (y + 1) / l.rows * this.worldHeight, -this.worldWidth / 2 + (x + 1) / l.columns * this.worldWidth, this.worldHeight / 2 - y / l.rows * this.worldHeight];
    if (tile.bounds.some((n, i) => Math.abs(n - expected[i]) > .5)) throw new Error('Contour bounds do not match tile ownership');
    return tile;
  }
  async prepare() {
    try { await Promise.all(Array.from({ length: this.pyramid.levels[0].rows }, (_, y) => Array.from({ length: this.pyramid.levels[0].columns }, async (_, x) => {
      const key = this.key(0, x, y), controller = new AbortController(); this.active.set(key, controller);
      try { const tile = await this.load(0, x, y, controller.signal); if (!this.disposed) this.store(key, tile); }
      finally { this.active.delete(key); }
    })).flat()); } catch (error) { this.destroy(); throw error; }
  }
  private store(key: string, tile: ContourTile) { this.failed.delete(key); this.cache.set(key, { tile, ready: performance.now(), used: ++this.usage, bytes: 256 + tile.paths.reduce((sum, p) => sum + p.points.byteLength + 128 + p.id.length * 2, 0) }); }
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
  draw(ctx: CanvasRenderingContext2D, transform: ContourTransform) {
    if (this.disposed || !this.cache.size) return;
    const destination = ctx;
    this.buffer ??= document.createElement('canvas');
    const ratio = Math.min(transform.dpr ?? 1, 4096 / Math.max(transform.width, transform.height));
    const bw = Math.max(1, Math.ceil(transform.width * ratio)), bh = Math.max(1, Math.ceil(transform.height * ratio));
    if (this.buffer.width !== bw || this.buffer.height !== bh) { this.buffer.width = bw; this.buffer.height = bh; }
    const bufferContext = this.buffer.getContext('2d'); if (!bufferContext) throw new Error('Unable to allocate contour drawing buffer');
    ctx = bufferContext; ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, transform.width, transform.height);
    // Add weighted transparent stroke coverage, then composite once over the basemap.
    ctx.globalCompositeOperation = 'lighter';
    const level = Math.min(this.pyramid.levels.length - 1, Math.max(0, Math.ceil(Math.log2(Math.max(this.worldWidth, this.worldHeight) * transform.scale / 1024))));
    if (level !== this.lastLevel) { this.outgoingLevel = this.lastLevel; this.levelChanged = performance.now(); this.lastLevel = level; }
    const { columns, rows } = this.pyramid.levels[level], nw = transform.screenToWorld(0, 0), se = transform.screenToWorld(transform.width, transform.height);
    const minX = Math.max(0, Math.floor((nw.x + this.worldWidth / 2) / this.worldWidth * columns)), maxX = Math.min(columns - 1, Math.floor((se.x + this.worldWidth / 2) / this.worldWidth * columns));
    const minY = Math.max(0, Math.floor((this.worldHeight / 2 - nw.z) / this.worldHeight * rows)), maxY = Math.min(rows - 1, Math.floor((this.worldHeight / 2 - se.z) / this.worldHeight * rows));
    this.queue = []; this.visible.clear(); const labels: { path: ContourPath; region: [number, number, number, number] }[] = []; let fading = false;
    for (let y = minY; y <= maxY; y++) for (let x = minX; x <= maxX; x++) {
      const key = this.key(level, x, y); this.visible.add(key);
      const failure = this.failed.get(key);
      if (!this.cache.has(key) && !this.active.has(key) && (!failure || failure.attempts < 3 && failure.retryAt <= performance.now())) this.queue.push({ key, z: level, x, y });
      const a = transform.worldToScreen(-this.worldWidth / 2 + x / columns * this.worldWidth, this.worldHeight / 2 - y / rows * this.worldHeight), b = transform.worldToScreen(-this.worldWidth / 2 + (x + 1) / columns * this.worldWidth, this.worldHeight / 2 - (y + 1) / rows * this.worldHeight);
      let entry: Entry | undefined, parent: Entry | undefined;
      for (let z = level; z >= 0; z--) { const k = this.key(z, Math.floor(x / 2 ** (level - z)), Math.floor(y / 2 ** (level - z))); this.visible.add(k); const e = this.cache.get(k); if (e) { if (!entry) entry = e; else { parent = e; break; } } }
      if (!entry) continue; entry.used = ++this.usage;
      const alpha = Math.min(1, (performance.now() - entry.ready) / 180); fading ||= alpha < 1;
      ctx.save(); ctx.beginPath(); ctx.rect(a.x, a.y, b.x - a.x, b.y - a.y); ctx.clip();
      if (parent && alpha < 1) this.lines(ctx, parent.tile.paths, transform, 1 - alpha);
      this.lines(ctx, entry.tile.paths, transform, parent ? alpha : 1); ctx.restore();
      // Zooming out keeps cached finer detail briefly above the coarser replacement.
      const outgoingAlpha = Math.max(0, 1 - (performance.now() - this.levelChanged) / 180);
      if (outgoingAlpha > 0 && this.outgoingLevel > level) for (const [childKey, child] of this.cache) {
        const [cz, cx, cy] = childKey.split('/').map(Number);
        if (cz !== this.outgoingLevel || Math.floor(cx / 2 ** (cz - level)) !== x || Math.floor(cy / 2 ** (cz - level)) !== y) continue;
        const childLevel = this.pyramid.levels[cz];
        const ca = transform.worldToScreen(-this.worldWidth / 2 + cx / childLevel.columns * this.worldWidth, this.worldHeight / 2 - cy / childLevel.rows * this.worldHeight), cb = transform.worldToScreen(-this.worldWidth / 2 + (cx + 1) / childLevel.columns * this.worldWidth, this.worldHeight / 2 - (cy + 1) / childLevel.rows * this.worldHeight);
        this.visible.add(childKey); fading = true;
        ctx.save(); ctx.beginPath(); ctx.rect(ca.x, ca.y, cb.x - ca.x, cb.y - ca.y); ctx.clip(); ctx.clearRect(ca.x, ca.y, cb.x - ca.x, cb.y - ca.y); this.lines(ctx, entry.tile.paths, transform, 1 - outgoingAlpha); this.lines(ctx, child.tile.paths, transform, outgoingAlpha); ctx.restore();
      }
      for (const path of entry.tile.paths) if (this.index(path)) labels.push({ path, region: [a.x, a.y, b.x, b.y] });
    }
    for (const [key, controller] of this.active) if (!key.startsWith('0/') && !this.visible.has(key)) { controller.abort(); this.active.delete(key); }
    this.pump(); this.evict(); this.scheduleRetry(); destination.drawImage(this.buffer, 0, 0, bw, bh, 0, 0, transform.width, transform.height); this.labels(destination, labels, transform);
    if (fading && !this.frame) this.frame = requestAnimationFrame(() => { this.frame = 0; if (!this.disposed) this.redraw(); });
  }
  private index(p: ContourPath) { return p.isIndex ?? Math.abs(p.elevationMeters / (this.interval * 5) - Math.round(p.elevationMeters / (this.interval * 5))) < 1e-6; }
  private lines(ctx: CanvasRenderingContext2D, paths: ContourPath[], t: ContourTransform, alpha: number) {
    ctx.save(); ctx.globalAlpha = alpha; ctx.strokeStyle = '#967d61'; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    for (const path of paths) {
      ctx.lineWidth = this.index(path) ? 1.1 : .55; ctx.beginPath();
      for (let i = 0; i < path.points.length; i += 2) { const p = t.worldToScreen(path.points[i], path.points[i + 1]); if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y); }
      ctx.stroke();
    }
    ctx.restore();
  }
  private labels(ctx: CanvasRenderingContext2D, candidates: { path: ContourPath; region: [number, number, number, number] }[], t: ContourTransform) {
    const occupied: [number, number, number, number][] = [], counts = new Map<string, number>();
    ctx.save(); ctx.font = '10px system-ui'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = '#80684f'; ctx.strokeStyle = '#f6f4ed'; ctx.lineWidth = 3;
    for (const { path, region } of candidates) {
      const text = `${path.elevationMeters} m`, textWidth = ctx.measureText(text).width;
      const points = Array.from({ length: path.points.length / 2 }, (_, i) => t.worldToScreen(path.points[i * 2], path.points[i * 2 + 1]));
      for (const { x, y, angle } of contourLabelStations(points, 320, [Math.max(0, region[0]), Math.max(0, region[1]), Math.min(t.width, region[2]), Math.min(t.height, region[3])])) {
        if ((counts.get(path.id) ?? 0) >= 3) break;
        const radiusX = Math.abs(Math.cos(angle)) * (textWidth / 2 + 8) + Math.abs(Math.sin(angle)) * 10, radiusY = Math.abs(Math.sin(angle)) * (textWidth / 2 + 8) + Math.abs(Math.cos(angle)) * 10;
        const box: [number, number, number, number] = [x - radiusX, y - radiusY, x + radiusX, y + radiusY];
        if (box[0] < Math.max(16, region[0]) || box[1] < Math.max(16, region[1]) || box[2] > Math.min(t.width - 16, region[2]) || box[3] > Math.min(t.height - 16, region[3]) || occupied.some(o => box[0] < o[2] && box[2] > o[0] && box[1] < o[3] && box[3] > o[1])) continue;
        occupied.push(box); counts.set(path.id, (counts.get(path.id) ?? 0) + 1);
        ctx.save(); ctx.translate(x, y); ctx.rotate(angle); ctx.strokeText(text, 0, 0); ctx.fillText(text, 0, 0); ctx.restore();
      }
    }
    ctx.restore();
  }
  private evict() {
    let bytes = [...this.cache.values()].reduce((n, e) => n + e.bytes, 0);
    for (const [key, entry] of [...this.cache].sort((a, b) => a[1].used - b[1].used)) { if (bytes <= 32 * 1024 * 1024) break; if (key.startsWith('0/') || this.visible.has(key)) continue; this.cache.delete(key); bytes -= entry.bytes; }
  }
  destroy() { this.disposed = true; if (this.frame) cancelAnimationFrame(this.frame); if (this.retryTimer !== undefined) clearTimeout(this.retryTimer); for (const controller of this.active.values()) controller.abort(); this.active.clear(); this.cache.clear(); this.queue = []; this.failed.clear(); if (this.buffer) { this.buffer.width = 0; this.buffer.height = 0; this.buffer = undefined; } }
}
