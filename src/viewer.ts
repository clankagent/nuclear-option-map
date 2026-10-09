import init, { MapViewport } from '../pkg/nuclear_option_map.js';
import { validateMap, validTint, type MapDefinition } from './catalog';

export type MapMode = 'builtin' | 'raw';
export interface ViewState {
  map: MapDefinition | null; mode: MapMode; grid: boolean; tint: string;
  zoom: number; scale: number; centerX: number; centerZ: number; width: number; height: number;
}
export interface MapPointer { x: number; z: number; insideMap: boolean }
export interface MapViewerOptions {
  map?: MapDefinition; mode?: MapMode; grid?: boolean; tint?: string;
  onViewChange?: (state: ViewState) => void;
  onPointerMove?: (position: MapPointer | null) => void;
}
let wasmReady: Promise<unknown> | undefined;
const constructionToken = Symbol('MapViewer factory');
export async function createMapViewer(canvas: HTMLCanvasElement, options: MapViewerOptions = {}): Promise<MapViewer> {
  return MapViewer.create(canvas, options);
}

/** Browser canvas adapter. Coordinates and viewport transforms are implemented by the Rust WASM core. */
export class MapViewer {
  private viewport: MapViewport;
  private ctx: CanvasRenderingContext2D;
  private map: MapDefinition | null = null;
  private source: HTMLImageElement | null = null;
  private tinted: HTMLCanvasElement | null = null;
  private mode: MapMode;
  private grid: boolean;
  private tint: string;
  private customTint: boolean;
  private width = 1;
  private height = 1;
  private disposed = false;
  private generation = 0;
  private observer?: ResizeObserver;
  private cleanups: (() => void)[] = [];
  private pointers = new Map<number, { x: number; y: number }>();
  private previousTouchAction: string;
  private previousTabIndex: string | null;

  static async create(canvas: HTMLCanvasElement, options: MapViewerOptions = {}): Promise<MapViewer> {
    await (wasmReady ??= init().catch(error => { wasmReady = undefined; throw error; }));
    const viewer = new MapViewer(canvas, options, constructionToken);
    try {
      viewer.setup();
      if (options.map) await viewer.setMap(options.map);
      else viewer.changed();
      return viewer;
    } catch (error) { viewer.destroy(); throw error; }
  }
  private constructor(private canvas: HTMLCanvasElement, private options: MapViewerOptions, token: symbol) {
    if (token !== constructionToken) throw new TypeError('Use await createMapViewer(canvas, options) to construct a MapViewer');
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('A 2D canvas context is required');
    this.ctx = ctx;
    this.mode = options.mode ?? 'builtin';
    this.grid = options.grid ?? true;
    this.tint = options.tint ?? '#819987';
    this.customTint = options.tint !== undefined;
    if (!validTint(this.tint)) throw new TypeError('Tint must be #RRGGBB');
    if (!['builtin', 'raw'].includes(this.mode)) throw new TypeError('Unknown map mode');
    this.viewport = new MapViewport(1, 1, 1, 1);
    this.previousTouchAction = canvas.style.touchAction;
    this.previousTabIndex = canvas.getAttribute('tabindex');
  }
  private setup() {
    const canvas = this.canvas;
    canvas.style.touchAction = 'none';
    if (canvas.tabIndex < 0) canvas.tabIndex = 0;
    this.listen('wheel', ((e: WheelEvent) => {
      e.preventDefault(); const p = this.local(e);
      this.viewport.zoomAt(Math.exp(-Math.max(-500, Math.min(500, e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? this.height : 1))) * 0.0015), p.x, p.y); this.changed();
    }) as EventListener, { passive: false });
    this.listen('pointerdown', ((e: PointerEvent) => {
      if (e.button !== 0) return;
      canvas.focus({ preventScroll: true }); canvas.setPointerCapture(e.pointerId);
      this.pointers.set(e.pointerId, this.local(e));
    }) as EventListener);
    this.listen('pointermove', ((e: PointerEvent) => {
      const point = this.local(e), old = this.pointers.get(e.pointerId);
      if (old) {
        const other = [...this.pointers.entries()].find(([id]) => id !== e.pointerId)?.[1];
        if (other && this.pointers.size === 2) {
          const before = Math.hypot(old.x - other.x, old.y - other.y);
          const after = Math.hypot(point.x - other.x, point.y - other.y);
          const midpoint = { x: (old.x + other.x) / 2, y: (old.y + other.y) / 2 };
          if (before > 1 && after > 1) this.viewport.zoomAt(after / before, midpoint.x, midpoint.y);
          this.viewport.pan((point.x - old.x) / 2, (point.y - old.y) / 2);
        } else this.viewport.pan(point.x - old.x, point.y - old.y);
        this.pointers.set(e.pointerId, point); this.changed();
      }
      const world = this.screenToWorld(point.x, point.y);
      this.options.onPointerMove?.({ ...world, insideMap: !!this.map && Math.abs(world.x) <= this.map.widthMeters / 2 && Math.abs(world.z) <= this.map.heightMeters / 2 });
    }) as EventListener);
    const release = ((e: PointerEvent) => { this.pointers.delete(e.pointerId); }) as EventListener;
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) this.listen(name, release);
    this.listen('pointerleave', (() => this.options.onPointerMove?.(null)) as EventListener);
    this.listen('keydown', ((e: KeyboardEvent) => {
      const pan: Record<string, [number, number]> = { ArrowLeft: [60, 0], ArrowRight: [-60, 0], ArrowUp: [0, 60], ArrowDown: [0, -60] };
      if (pan[e.key]) { e.preventDefault(); this.viewport.pan(...pan[e.key]); this.changed(); }
      else if (e.key === '+' || e.key === '=') { e.preventDefault(); this.zoomIn(); }
      else if (e.key === '-') { e.preventDefault(); this.zoomOut(); }
      else if (e.key === '0') { e.preventDefault(); this.fit(); }
    }) as EventListener);
    this.observer = new ResizeObserver(() => this.resize()); this.observer.observe(canvas);
    const onResize = () => this.resize(); window.addEventListener('resize', onResize);
    this.cleanups.push(() => window.removeEventListener('resize', onResize));
    this.resize(false);
  }
  private listen(name: string, fn: EventListener, options?: AddEventListenerOptions) {
    this.canvas.addEventListener(name, fn, options); this.cleanups.push(() => this.canvas.removeEventListener(name, fn, options));
  }
  private assertAlive() { if (this.disposed) throw new Error('MapViewer has been destroyed'); }
  private local(e: MouseEvent) { const r = this.canvas.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }
  get state(): ViewState {
    return { map: this.map ? validateMap(this.map) : null, mode: this.mode, grid: this.grid, tint: this.tint, zoom: this.viewport.zoom(), scale: this.viewport.scale(), centerX: this.viewport.centerX(), centerZ: this.viewport.centerZ(), width: this.width, height: this.height };
  }
  worldToScreen(x: number, z: number) { this.assertAlive(); const p = this.viewport.worldToScreen(x, z); return { x: p[0], y: p[1] }; }
  screenToWorld(x: number, y: number) { this.assertAlive(); const p = this.viewport.screenToWorld(x, y); return { x: p[0], z: p[1] }; }
  async setMap(value: MapDefinition): Promise<void> {
    this.assertAlive(); const map = validateMap(value), token = ++this.generation;
    const image = new Image(); image.crossOrigin = 'anonymous';
    await new Promise<void>((resolve, reject) => { image.onload = () => resolve(); image.onerror = () => reject(new Error(`Unable to load map image: ${map.image}`)); image.src = map.image; });
    if (this.disposed || token !== this.generation) return;
    const tint = this.customTint ? this.tint : map.tint ?? '#819987';
    // Prepare pixels before changing the active map: failed/CORS-blocked loads leave it usable.
    const tinted = this.makeTinted(image, tint);
    const viewport = new MapViewport(map.widthMeters, map.heightMeters, this.width, this.height);
    this.viewport.free(); this.viewport = viewport; this.map = map; this.source = image;
    this.tint = tint; this.tinted = tinted; this.changed();
  }
  zoomIn() { this.zoomBy(1.5); }
  zoomOut() { this.zoomBy(1 / 1.5); }
  private zoomBy(factor: number) { this.assertAlive(); this.viewport.zoomAt(factor, this.width / 2, this.height / 2); this.changed(); }
  fit() { this.assertAlive(); this.viewport.fit(); this.changed(); }
  setGrid(value: boolean) { this.assertAlive(); this.grid = value; this.changed(); }
  setMode(value: MapMode) { this.assertAlive(); if (value !== 'builtin' && value !== 'raw') throw new TypeError('Unknown map mode'); this.mode = value; this.changed(); }
  setTint(value: string) { this.assertAlive(); if (!validTint(value)) throw new TypeError('Tint must be #RRGGBB'); const tinted = this.source ? this.makeTinted(this.source, value) : null; this.tint = value; this.customTint = true; this.tinted = tinted; this.changed(); }
  private makeTinted(source: HTMLImageElement, tint: string): HTMLCanvasElement {
    const canvas = document.createElement('canvas'); canvas.width = source.naturalWidth; canvas.height = source.naturalHeight;
    const ctx = canvas.getContext('2d')!; ctx.drawImage(source, 0, 0);
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const channels = [1, 3, 5].map(start => parseInt(tint.slice(start, start + 2), 16) / 255);
    for (let i = 0; i < data.data.length; i += 4) for (let c = 0; c < 3; c++) data.data[i + c] = Math.round(data.data[i + c] * channels[c]);
    ctx.putImageData(data, 0, 0); return canvas;
  }
  private resize(notify = true) {
    if (this.disposed) return;
    const r = this.canvas.getBoundingClientRect(); this.width = Math.max(1, r.width); this.height = Math.max(1, r.height);
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(this.width * dpr); this.canvas.height = Math.round(this.height * dpr);
    this.viewport.resize(this.width, this.height); if (notify) this.changed(); else this.draw();
  }
  private changed() { this.draw(); this.options.onViewChange?.(this.state); }
  private draw() {
    const ctx = this.ctx; ctx.setTransform(this.canvas.width / this.width, 0, 0, this.canvas.height / this.height, 0, 0);
    ctx.fillStyle = '#111a1a'; ctx.fillRect(0, 0, this.width, this.height);
    if (!this.map || !this.source) return;
    const m = this.map, a = this.worldToScreen(-m.widthMeters / 2, m.heightMeters / 2), b = this.worldToScreen(m.widthMeters / 2, -m.heightMeters / 2);
    ctx.drawImage(this.mode === 'builtin' ? this.tinted! : this.source, a.x, a.y, b.x - a.x, b.y - a.y);
    if (!this.grid) return;
    ctx.save(); ctx.beginPath(); ctx.rect(a.x, a.y, b.x - a.x, b.y - a.y); ctx.clip();
    ctx.strokeStyle = 'rgba(216,235,224,.3)'; ctx.fillStyle = '#d8ebe0'; ctx.lineWidth = 1; ctx.font = '11px system-ui';
    const dx = 10000, dz = 10000;
    const left = -m.grid.offsetX, top = m.grid.offsetY;
    const visibleNW = this.screenToWorld(0, 0), visibleSE = this.screenToWorld(this.width, this.height);
    const startCol = Math.max(0, Math.floor((visibleNW.x - left) / dx));
    const endCol = Math.min(m.grid.columns, Math.ceil((visibleSE.x - left) / dx));
    const startRow = Math.max(0, Math.floor((top - visibleNW.z) / dz));
    const endRow = Math.min(m.grid.rows, Math.ceil((top - visibleSE.z) / dz));
    if (dx * this.viewport.scale() >= 18) for (let col = startCol; col <= endCol; col++) {
      const x = this.worldToScreen(left + col * dx, 0).x; ctx.beginPath(); ctx.moveTo(x, a.y); ctx.lineTo(x, b.y); ctx.stroke();
    }
    if (dz * this.viewport.scale() >= 18) for (let row = startRow; row <= endRow; row++) {
      const y = this.worldToScreen(0, top - row * dz).y; ctx.beginPath(); ctx.moveTo(a.x, y); ctx.lineTo(b.x, y); ctx.stroke();
    }
    if (dx * this.viewport.scale() >= 64 && dz * this.viewport.scale() >= 48) {
      ctx.shadowColor = '#111a1a'; ctx.shadowBlur = 3;
      for (let row = startRow; row < endRow; row++) for (let col = startCol; col < endCol; col++) {
        const p = this.worldToScreen(left + (col + .5) * dx, top - (row + .5) * dz);
        let letters = '', n = row + 1;
        while (n > 0) { n--; letters = String.fromCharCode(65 + n % 26) + letters; n = Math.floor(n / 26); }
        ctx.fillText(`${letters}${col}`, p.x - 8, p.y + 4);
      }
    }
    ctx.restore();
  }
  destroy() {
    if (this.disposed) return; this.disposed = true; this.generation++;
    this.observer?.disconnect(); this.cleanups.forEach(fn => fn());
    for (const id of this.pointers.keys()) if (this.canvas.hasPointerCapture(id)) this.canvas.releasePointerCapture(id);
    this.pointers.clear(); this.viewport.free(); this.source = null; this.tinted = null;
    this.canvas.style.touchAction = this.previousTouchAction;
    if (this.previousTabIndex === null) this.canvas.removeAttribute('tabindex'); else this.canvas.setAttribute('tabindex', this.previousTabIndex);
  }
}
