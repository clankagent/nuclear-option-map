import type { TilePyramid, PyramidLevel } from './catalog';
export interface TileImage { image: CanvasImageSource; width: number; height: number; dispose(): void }
export type TileLoader = (url: string, signal: AbortSignal) => Promise<TileImage>;
interface Tile { key: string; level: number; x: number; y: number; image: TileImage; ready: number; used: number; bytes: number }
interface Request { key: string; level: number; x: number; y: number; priority: number }
export interface TileStats { pending: number; visiblePending: number; cached: number; level: number; bytes: number }
export interface RasterView { x: number; y: number; width: number; height: number; screenWidth: number; screenHeight: number; dpr: number }
/** Progressive raster cache: the caller paints a pinned overview underneath every frame. */
export class PyramidRenderer {
  private cache = new Map<string, Tile>();
  private active = new Map<string, AbortController>();
  // Cancellation is advisory: a decoder may settle after abort. Keep its slot until then.
  private inflight = new Set<AbortController>();
  private queue: Request[] = [];
  private failed = new Map<string, { attempts: number; retryAt: number }>();
  private wanted = new Set<string>();
  private visible = new Set<string>();
  private protected = new Set<string>();
  private level = 0;
  private disposed = false;
  private clock = 0;
  private frame = 0;
  private retryTimer: ReturnType<typeof setTimeout> | undefined;
  constructor(private pyramid: TilePyramid, private loader: TileLoader, private redraw: () => void, private budget = 128 * 1024 * 1024, private now = () => performance.now(), private hardBudget = false) {}
  get stats(): TileStats { return { pending: this.queue.length + this.active.size, visiblePending: [...this.visible].filter(key => !this.cache.has(key)).length, cached: this.cache.size, level: this.level, bytes: [...this.cache.values()].reduce((n, t) => n + t.bytes, 0) }; }
  private key(level: number, x: number, y: number) { return `${level}/${x}/${y}`; }
  update(v: RasterView) {
    if (this.disposed) return;
    const density = Math.max(v.width * v.dpr, v.height * v.dpr);
    const ideal = this.pyramid.levels.findIndex(l => Math.max(l.width, l.height) >= density);
    const next = ideal < 0 ? this.pyramid.levels.length - 1 : ideal;
    const current = this.pyramid.levels[this.level];
    if (next > this.level || density < Math.max(current.width, current.height) * .45) this.level = next;
    // Reserve room for fallback ancestors and queued completions on large/high-DPR viewports.
    while (this.level > 0) {
      const candidate = this.pyramid.levels[this.level], size = this.pyramid.tileSize;
      const cols = Math.min(candidate.columns, Math.ceil(v.screenWidth / v.width * candidate.width / size) + 2);
      const rows = Math.min(candidate.rows, Math.ceil(v.screenHeight / v.height * candidate.height / size) + 2);
      if (cols * rows * size * size * 4 <= this.budget * .6) break;
      this.level--;
    }
    const l = this.pyramid.levels[this.level], size = this.pyramid.tileSize;
    const minX = Math.max(0, Math.floor(-v.x / v.width * l.width / size));
    const maxX = Math.min(l.columns - 1, Math.floor((v.screenWidth - v.x) / v.width * l.width / size));
    const minY = Math.max(0, Math.floor(-v.y / v.height * l.height / size));
    const maxY = Math.min(l.rows - 1, Math.floor((v.screenHeight - v.y) / v.height * l.height / size));
    this.wanted.clear(); this.visible.clear(); this.protected.clear(); const requests = new Map<string, Request>();
    const want = (level: number, x: number, y: number, priority: number) => {
      const key = this.key(level, x, y); this.wanted.add(key);
      if (priority < 100000) this.visible.add(key);
      if (!requests.has(key)) requests.set(key, { key, level, x, y, priority });
    };
    if (maxX >= minX && maxY >= minY) {
      for (let y = Math.max(0, minY - 1); y <= Math.min(l.rows - 1, maxY + 1); y++) for (let x = Math.max(0, minX - 1); x <= Math.min(l.columns - 1, maxX + 1); x++) {
        const visible = x >= minX && x <= maxX && y >= minY && y <= maxY;
        if (visible) {
          for (let z = this.level, ax = x, ay = y; z >= 0; z--, ax = Math.floor(ax / 2), ay = Math.floor(ay / 2)) {
            this.protected.add(this.key(z, ax, ay));
            // Direct zoom receives progressively sharper coverage instead of waiting for all detail.
            want(z, ax, ay, z === this.level ? Math.hypot(x - (minX + maxX) / 2, y - (minY + maxY) / 2) : -1000 + z);
          }
        }
        if (!visible) want(this.level, x, y, 100000 + Math.hypot(x - (minX + maxX) / 2, y - (minY + maxY) / 2));
      }
    }
    // Fine children remain coverage until the requested coarser tile finishes fading in.
    for (const tile of this.cache.values()) if (tile.level > this.level) {
      const parentKey = this.key(this.level, Math.floor(tile.x / 2 ** (tile.level - this.level)), Math.floor(tile.y / 2 ** (tile.level - this.level)));
      const parent = this.cache.get(parentKey);
      if (this.wanted.has(parentKey) && (!parent || this.now() - parent.ready < 180)) this.protected.add(tile.key);
    }
    for (const [key, controller] of this.active) if (!this.wanted.has(key)) { controller.abort(); this.active.delete(key); }
    // A new view may promote previous prefetch to visible. Preserve those loads, but free
    // prefetch slots immediately when uncovered visible work would otherwise wait behind them.
    if ([...this.visible].some(key => !this.cache.has(key) && !this.active.has(key))) {
      for (const [key, controller] of this.active) if (!this.visible.has(key)) { controller.abort(); this.active.delete(key); }
    }
    this.queue = [...requests.values()].filter(r => {
      const failure = this.failed.get(r.key);
      return !this.cache.has(r.key) && !this.active.has(r.key) && (!failure || failure.attempts < 3 && failure.retryAt <= this.now());
    }).sort((a, b) => a.priority - b.priority);
    this.evict(); this.pump(); this.scheduleRetry();
  }
  private scheduleRetry() {
    if (this.retryTimer !== undefined) clearTimeout(this.retryTimer);
    this.retryTimer = undefined;
    const pending = [...this.failed].filter(([key, failure]) => this.wanted.has(key) && failure.attempts < 3).map(([, failure]) => failure.retryAt);
    if (pending.length) this.retryTimer = setTimeout(() => { this.retryTimer = undefined; if (!this.disposed) this.redraw(); }, Math.max(1, Math.min(...pending) - this.now()));
  }
  private pump() {
    while (!this.disposed && this.inflight.size < 6 && this.queue.length) {
      // Leave four slots available for view changes; speculative neighbors must not
      // compete with visible decode/network work or consume the whole cache budget.
      if (this.queue[0].priority >= 100000 && (this.stats.visiblePending > 0 || this.active.size >= 2)) break;
      if (this.hardBudget && this.queue[0].priority >= 100000 && this.stats.bytes + (this.active.size + 1) * (this.pyramid.tileSize + 2 * (this.pyramid.gutter ?? 0)) ** 2 * 4 > this.budget) break;
      const r = this.queue.shift()!, controller = new AbortController(); this.active.set(r.key, controller);
      this.inflight.add(controller);
      const l = this.pyramid.levels[r.level];
      const url = l.urlTemplate.replaceAll('{z}', String(r.level)).replaceAll('{x}', String(r.x)).replaceAll('{y}', String(r.y));
      void this.loader(url, controller.signal).then(image => {
        if (this.disposed || controller.signal.aborted) { image.dispose(); return; }
        const width = Math.min(this.pyramid.tileSize, l.width - r.x * this.pyramid.tileSize), height = Math.min(this.pyramid.tileSize, l.height - r.y * this.pyramid.tileSize);
        const gutter = this.pyramid.gutter ?? 0;
        if (image.width !== width + gutter * 2 || image.height !== height + gutter * 2) { image.dispose(); throw new Error('Tile dimensions do not match pyramid'); }
        this.cache.set(r.key, { ...r, image, ready: this.now(), used: ++this.clock, bytes: image.width * image.height * 4 });
        this.failed.delete(r.key);
        this.evict(); this.redraw();
      }).catch(() => {
        if (!this.disposed && !controller.signal.aborted) {
          const attempts = (this.failed.get(r.key)?.attempts ?? 0) + 1;
          this.failed.set(r.key, { attempts, retryAt: this.now() + 500 * 2 ** (attempts - 1) }); this.scheduleRetry();
        }
      }).finally(() => { this.inflight.delete(controller); if (this.active.get(r.key) === controller) this.active.delete(r.key); this.pump(); });
    }
  }
  draw(ctx: CanvasRenderingContext2D, v: RasterView) {
    const now = this.now(); let fading = false;
    const opaque = new Set<string>(), branches = new Set<string>();
    for (const tile of this.cache.values()) if (tile.level <= this.level && now - tile.ready >= 180) {
      opaque.add(tile.key);
      for (let z = tile.level - 1, ax = Math.floor(tile.x / 2), ay = Math.floor(tile.y / 2); z >= 0; z--, ax = Math.floor(ax / 2), ay = Math.floor(ay / 2)) branches.add(this.key(z, ax, ay));
    }
    type Rect = [number, number, number, number];
    const holes = (level: number, tx: number, ty: number, root: boolean, output: Rect[]) => {
      const l = this.pyramid.levels[level], size = this.pyramid.tileSize;
      const x = v.x + tx * size / l.width * v.width, y = v.y + ty * size / l.height * v.height;
      const width = Math.min(size, l.width - tx * size) / l.width * v.width, height = Math.min(size, l.height - ty * size) / l.height * v.height;
      if (x + width <= 0 || y + height <= 0 || x >= v.screenWidth || y >= v.screenHeight) return;
      const key = this.key(level, tx, ty);
      if (!root && opaque.has(key)) return;
      // Branch only toward cached opaque descendants; missing levels never expand a full grid.
      if (level >= this.level || !branches.has(key)) { output.push([x, y, width, height]); return; }
      const next = this.pyramid.levels[level + 1];
      for (let cy = ty * 2; cy < Math.min(next.rows, ty * 2 + 2); cy++) for (let cx = tx * 2; cx < Math.min(next.columns, tx * 2 + 2); cx++) holes(level + 1, cx, cy, false, output);
    };
    // Coarse first, retained finer children above: both zoom directions keep loaded coverage.
    for (const tile of [...this.cache.values()].sort((a, b) => a.level - b.level)) {
      const l: PyramidLevel = this.pyramid.levels[tile.level], size = this.pyramid.tileSize;
      const x = v.x + tile.x * size / l.width * v.width, y = v.y + tile.y * size / l.height * v.height;
      const gutter = this.pyramid.gutter ?? 0, logicalWidth = tile.image.width - gutter * 2, logicalHeight = tile.image.height - gutter * 2;
      const width = logicalWidth / l.width * v.width, height = logicalHeight / l.height * v.height;
      if (x + width <= 0 || y + height <= 0 || x >= v.screenWidth || y >= v.screenHeight) continue;
      const regions: Rect[] = [];
      const partial = tile.level < this.level && branches.has(tile.key);
      if (partial) { holes(tile.level, tile.x, tile.y, true, regions); if (!regions.length) continue; }
      tile.used = ++this.clock;
      let alpha = Math.min(1, Math.max(0, (now - tile.ready) / 180)); fading ||= alpha < 1;
      if (tile.level > this.level) {
        const parent = this.cache.get(this.key(this.level, Math.floor(tile.x / 2 ** (tile.level - this.level)), Math.floor(tile.y / 2 ** (tile.level - this.level))));
        if (parent) { const transition = Math.min(1, Math.max(0, (now - parent.ready) / 180)); alpha *= 1 - transition; fading ||= transition < 1; }
      }
      if (alpha <= 0) continue;
      ctx.save(); ctx.globalAlpha = alpha;
      if (partial) { ctx.beginPath(); for (const region of regions) ctx.rect(...region); ctx.clip(); }
      ctx.drawImage(tile.image.image, gutter, gutter, logicalWidth, logicalHeight, x, y, width, height); ctx.restore();
    }
    if (fading && !this.frame) this.frame = requestAnimationFrame(() => { this.frame = 0; if (!this.disposed) this.redraw(); });
  }
  private evict() {
    let bytes = this.stats.bytes;
    for (const tile of [...this.cache.values()].sort((a, b) => a.used - b.used)) {
      if (bytes <= this.budget) break;
      if (this.protected.has(tile.key)) continue;
      this.cache.delete(tile.key); bytes -= tile.bytes; tile.image.dispose();
    }
    if (this.hardBudget && bytes > this.budget) {
      // Retained fine zoom-out coverage is expendable before current wanted coverage.
      // Keep coarse fallback first when the caller cannot fit all wanted detail.
      const candidates = [...this.cache.values()].sort((a, b) => Number(this.visible.has(a.key)) - Number(this.visible.has(b.key)) || b.level - a.level || a.used - b.used);
      for (const tile of candidates) {
        if (bytes <= this.budget) break;
        this.cache.delete(tile.key); bytes -= tile.bytes; tile.image.dispose();
      }
    }
  }
  destroy() {
    if (this.disposed) return; this.disposed = true;
    if (this.frame) cancelAnimationFrame(this.frame);
    if (this.retryTimer !== undefined) clearTimeout(this.retryTimer);
    for (const controller of this.active.values()) controller.abort();
    for (const tile of this.cache.values()) tile.image.dispose();
    this.cache.clear(); this.active.clear(); this.queue = []; this.failed.clear(); this.wanted.clear(); this.visible.clear(); this.protected.clear();
  }
}
