import init, { MapViewport } from '../pkg/nuclear_option_map.js';
import { gridReference, gridRowLabel, validateMap, validTint, type MapDefinition, type ContourInterval } from './catalog';
import { disposeRaster, drawRaster, tintRaster, type TiledRaster } from './tiled-raster';
import { PyramidRenderer, type TileStats } from './pyramid';
import { ContourRenderer } from './contours';

export type MapMode = 'builtin' | 'raw';
export type GridLabels = 'edges' | 'cells' | 'none';
export interface ViewState {
  map: MapDefinition | null; mode: MapMode; grid: boolean; gridLabels: GridLabels; tint: string;
  zoom: number; scale: number; centerX: number; centerZ: number; width: number; height: number;
  tileStats?: TileStats;
  contourInterval?: ContourInterval;
}
export interface MapPointer { x: number; z: number; insideMap: boolean; gridReference: string | null }
export interface MapViewerOptions {
  map?: MapDefinition; mode?: MapMode; grid?: boolean; gridLabels?: GridLabels; tint?: string;
  contourInterval?: ContourInterval;
  onViewChange?: (state: ViewState) => void;
  onPointerMove?: (position: MapPointer | null) => void;
}
let wasmReady: Promise<unknown> | undefined;
const constructionToken = Symbol('MapViewer factory');
/** Preserve a requested interval where available; fallback does not replace that preference. */
export function chooseContourInterval(available: ContourInterval[], preferred: ContourInterval): ContourInterval {
  if (![20, 50, 100].includes(preferred)) throw new TypeError('Contour interval must be 20, 50, or 100 meters');
  if (!available.length) throw new TypeError('No contour intervals available');
  return available.includes(preferred) ? preferred : available.includes(100) ? 100 : available[0];
}
export async function createMapViewer(canvas: HTMLCanvasElement, options: MapViewerOptions = {}): Promise<MapViewer> {
  return MapViewer.create(canvas, options);
}

/** Browser canvas adapter. Coordinates and viewport transforms are implemented by the Rust WASM core. */
export class MapViewer {
  private viewport: MapViewport;
  private ctx: CanvasRenderingContext2D;
  private map: MapDefinition | null = null;
  private source: HTMLImageElement | null = null;
  private tinted: TiledRaster | null = null;
  private pyramid: PyramidRenderer | null = null;
  private contours: ContourRenderer | null = null;
  private contourGeneration = 0;
  private contourPending: ContourRenderer | null = null;
  private preferredContourInterval: ContourInterval;
  private preparingContours = new Set<ContourRenderer>();
  private mode: MapMode;
  private grid: boolean;
  private gridLabels: GridLabels;
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
    this.preferredContourInterval = options.contourInterval ?? 100;
    if (![20, 50, 100].includes(this.preferredContourInterval)) throw new TypeError('Contour interval must be 20, 50, or 100 meters');
    this.gridLabels = options.gridLabels ?? 'edges';
    if (!['edges', 'cells', 'none'].includes(this.gridLabels)) throw new TypeError('Unknown grid label mode');
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
      this.options.onPointerMove?.({ ...world, insideMap: !!this.map && Math.abs(world.x) <= this.map.widthMeters / 2 && Math.abs(world.z) <= this.map.heightMeters / 2, gridReference: this.map ? gridReference(this.map, world.x, world.z) : null });
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
    return { map: this.map ? validateMap(this.map) : null, mode: this.mode, grid: this.grid, gridLabels: this.gridLabels, tint: this.tint, zoom: this.viewport.zoom(), scale: this.viewport.scale(), centerX: this.viewport.centerX(), centerZ: this.viewport.centerZ(), width: this.width, height: this.height, ...(this.pyramid ? { tileStats: this.pyramid.stats } : {}), ...(this.contours ? { contourInterval: this.contours.interval } : {}) };
  }
  worldToScreen(x: number, z: number) { this.assertAlive(); const p = this.viewport.worldToScreen(x, z); return { x: p[0], y: p[1] }; }
  screenToWorld(x: number, y: number) { this.assertAlive(); const p = this.viewport.screenToWorld(x, y); return { x: p[0], z: p[1] }; }
  async setMap(value: MapDefinition, options: { preserveView?: boolean } = {}): Promise<void> {
    this.assertAlive(); const map = validateMap(value), token = ++this.generation;
    for (const pending of this.preparingContours) pending.destroy(); this.preparingContours.clear();
    const image = new Image(); image.crossOrigin = 'anonymous';
    await new Promise<void>((resolve, reject) => { image.onload = () => resolve(); image.onerror = () => reject(new Error(`Unable to load map image: ${map.image}`)); image.src = map.image; });
    if (this.disposed || token !== this.generation) return;
    const tint = this.customTint ? this.tint : map.tint ?? '#819987';
    let contours: ContourRenderer | null = null;
    if (map.contours) {
      contours = new ContourRenderer(map.contours, chooseContourInterval(map.contours.intervals, this.preferredContourInterval), map.widthMeters, map.heightMeters, () => { if (!this.disposed) this.draw(); });
      this.preparingContours.add(contours);
      try { await contours.prepare(); } catch (error) { contours.destroy(); throw error; }
      finally { this.preparingContours.delete(contours); }
      if (this.disposed || token !== this.generation) { contours.destroy(); return; }
    }
    // Prepare pixels before changing the active map: failed/CORS-blocked loads leave it usable.
    if (!Number.isSafeInteger(image.naturalWidth) || image.naturalWidth <= 0 || !Number.isSafeInteger(image.naturalHeight) || image.naturalHeight <= 0) throw new TypeError('Map image dimensions must be positive safe integers');
    let tinted: TiledRaster | null;
    try { tinted = this.mode === 'builtin' ? tintRaster(image, tint) : null; } catch (error) { contours?.destroy(); throw error; }
    let viewport: MapViewport | undefined;
    try { viewport = new MapViewport(map.widthMeters, map.heightMeters, this.width, this.height);
    if (options.preserveView && this.map && this.map.widthMeters === map.widthMeters && this.map.heightMeters === map.heightMeters) {
      viewport.zoomAt(this.viewport.zoom(), this.width / 2, this.height / 2);
      viewport.pan(-this.viewport.centerX() * viewport.scale(), this.viewport.centerZ() * viewport.scale());
    }
    } catch (error) { viewport?.free(); disposeRaster(tinted); contours?.destroy(); throw error; }
    disposeRaster(this.tinted);
    this.pyramid?.destroy(); this.pyramid = null;
    this.contourGeneration++; this.contourPending?.destroy(); this.contourPending = null; this.contours?.destroy(); this.contours = contours;
    this.viewport.free(); this.viewport = viewport; this.map = map; this.source = image;
    this.tint = tint; this.tinted = tinted; this.resetPyramid(); this.changed();
  }
  zoomIn() { this.zoomBy(1.5); }
  zoomOut() { this.zoomBy(1 / 1.5); }
  private zoomBy(factor: number) { this.assertAlive(); this.viewport.zoomAt(factor, this.width / 2, this.height / 2); this.changed(); }
  fit() { this.assertAlive(); this.viewport.fit(); this.changed(); }
  setGrid(value: boolean) { this.assertAlive(); this.grid = value; this.changed(); }
  setGridLabels(value: GridLabels) { this.assertAlive(); if (!['edges', 'cells', 'none'].includes(value)) throw new TypeError('Unknown grid label mode'); this.gridLabels = value; this.changed(); }
  async setContourInterval(interval: ContourInterval): Promise<void> {
    this.assertAlive();
    if (!this.map?.contours || !this.map.contours.intervals.includes(interval)) throw new TypeError('Contour interval is unavailable for this map');
    if (this.contours?.interval === interval && !this.contourPending) return;
    const token = ++this.contourGeneration; this.contourPending?.destroy();
    const next = new ContourRenderer(this.map.contours, interval, this.map.widthMeters, this.map.heightMeters, () => { if (!this.disposed) this.draw(); }); this.contourPending = next;
    try { await next.prepare(); }
    catch (error) { next.destroy(); if (token === this.contourGeneration) this.contourPending = null; throw error; }
    if (this.disposed || token !== this.contourGeneration) { next.destroy(); return; }
    this.contours?.destroy(); this.contours = next; this.contourPending = null; this.preferredContourInterval = interval; this.changed();
  }
  setMode(value: MapMode) { this.assertAlive(); if (value !== 'builtin' && value !== 'raw') throw new TypeError('Unknown map mode'); if (value === 'builtin' && this.source && !this.tinted) this.tinted = tintRaster(this.source, this.tint); this.mode = value; this.resetPyramid(); this.changed(); }
  setTint(value: string) { this.assertAlive(); if (!validTint(value)) throw new TypeError('Tint must be #RRGGBB'); const tinted = this.source && this.mode === 'builtin' ? tintRaster(this.source, value) : null; disposeRaster(this.tinted); this.tint = value; this.customTint = true; this.tinted = tinted; this.resetPyramid(); this.changed(); }
  private resetPyramid() {
    this.pyramid?.destroy(); this.pyramid = null;
    if (!this.map?.tiles) return;
    const mode = this.mode, tint = this.tint;
    this.pyramid = new PyramidRenderer(this.map.tiles, async (url, signal) => {
      const response = await fetch(url, { signal }); if (!response.ok) throw new Error(`Tile request failed (${response.status})`);
      const bitmap = await createImageBitmap(await response.blob());
      if (signal.aborted) { bitmap.close(); throw new Error('Tile request aborted'); }
      if (mode === 'raw') return { image: bitmap, width: bitmap.width, height: bitmap.height, dispose: () => bitmap.close() };
      try {
        const raster = tintRaster(bitmap, tint), canvas = raster.tiles[0].canvas;
        return { image: canvas, width: bitmap.width, height: bitmap.height, dispose: () => disposeRaster(raster) };
      } finally { bitmap.close(); }
    }, () => { if (!this.disposed) this.draw(); });
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
    ctx.imageSmoothingQuality = 'high';
    if (this.mode === 'builtin' && this.tinted) drawRaster(ctx, this.tinted, a.x, a.y, b.x - a.x, b.y - a.y, this.width, this.height);
    else ctx.drawImage(this.source, a.x, a.y, b.x - a.x, b.y - a.y);
    if (this.pyramid) {
      const view = { x: a.x, y: a.y, width: b.x - a.x, height: b.y - a.y, screenWidth: this.width, screenHeight: this.height, dpr: this.canvas.width / this.width };
      this.pyramid.update(view); this.pyramid.draw(ctx, view);
    }
    this.contours?.draw(ctx, { width: this.width, height: this.height, scale: this.viewport.scale(), dpr: this.canvas.width / this.width, worldToScreen: (x, z) => this.worldToScreen(x, z), screenToWorld: (x, y) => this.screenToWorld(x, y) });
    if (!this.grid) return;
    ctx.save(); ctx.beginPath(); ctx.rect(a.x, a.y, b.x - a.x, b.y - a.y); ctx.clip();
    ctx.strokeStyle = 'rgba(216,235,224,.16)'; ctx.fillStyle = 'rgba(216,235,224,.58)'; ctx.lineWidth = 1; ctx.font = '10px system-ui';
    const dx = 10000, dz = 10000;
    const left = -m.grid.offsetX, top = m.grid.offsetY;
    const clipLeft = Math.max(0, a.x), clipRight = Math.min(this.width, b.x), clipTop = Math.max(0, a.y), clipBottom = Math.min(this.height, b.y);
    const visibleNW = this.screenToWorld(clipLeft, clipTop), visibleSE = this.screenToWorld(clipRight, clipBottom);
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
    if (this.gridLabels !== 'none' && dx * this.viewport.scale() >= (this.gridLabels === 'edges' ? 18 : 64) && dz * this.viewport.scale() >= (this.gridLabels === 'edges' ? 18 : 48) && clipRight - clipLeft > 48 && clipBottom - clipTop > 36) {
      ctx.shadowColor = '#111a1a'; ctx.shadowBlur = 3;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      const centerCol = (col: number) => {
        const x = this.worldToScreen(left + col * dx, 0).x;
        return (Math.max(clipLeft, x) + Math.min(clipRight, x + dx * this.viewport.scale())) / 2;
      };
      const centerRow = (row: number) => {
        const y = this.worldToScreen(0, top - row * dz).y;
        return (Math.max(clipTop, y) + Math.min(clipBottom, y + dz * this.viewport.scale())) / 2;
      };
      if (this.gridLabels === 'cells') {
        for (let row = startRow; row < endRow; row++) for (let col = startCol; col < endCol; col++) ctx.fillText(`${gridRowLabel(row)}${col}`, centerCol(col), centerRow(row));
      } else {
        for (let col = startCol; col < endCol; col++) {
          const x = centerCol(col);
          if (x >= clipLeft + 20 && x <= clipRight - 20) { ctx.fillText(String(col), x, clipTop + 11); ctx.fillText(String(col), x, clipBottom - 11); }
        }
        for (let row = startRow; row < endRow; row++) {
          const y = centerRow(row);
          if (y >= clipTop + 20 && y <= clipBottom - 20) { ctx.fillText(gridRowLabel(row), clipLeft + 14, y); ctx.fillText(gridRowLabel(row), clipRight - 14, y); }
        }
      }
    }
    ctx.restore();
  }
  destroy() {
    if (this.disposed) return; this.disposed = true; this.generation++;
    this.observer?.disconnect(); this.cleanups.forEach(fn => fn());
    for (const id of this.pointers.keys()) if (this.canvas.hasPointerCapture(id)) this.canvas.releasePointerCapture(id);
    this.pointers.clear(); this.viewport.free(); this.source = null; disposeRaster(this.tinted); this.tinted = null; this.pyramid?.destroy(); this.pyramid = null;
    this.contourGeneration++; this.contourPending?.destroy(); this.contours?.destroy(); this.contours = null; this.contourPending = null;
    for (const pending of this.preparingContours) pending.destroy(); this.preparingContours.clear();
    this.canvas.style.touchAction = this.previousTouchAction;
    if (this.previousTabIndex === null) this.canvas.removeAttribute('tabindex'); else this.canvas.setAttribute('tabindex', this.previousTabIndex);
  }
}
