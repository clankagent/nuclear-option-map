import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
const core = vi.hoisted(() => ({ init: vi.fn(async () => undefined), free: vi.fn(), scale: 1 }));
const contour = vi.hoisted(() => ({ fail: false, waitInterval: null as number | null, resolvers: [] as (() => void)[] }));
vi.mock('../../src/contours', () => ({ ContourRenderer: class {
  private reject?: (error: Error) => void;
  constructor(_p: unknown, readonly interval: number) {}
  async prepare() { if (contour.fail) throw new Error('contour load failed'); if (contour.waitInterval === this.interval) await new Promise<void>((resolve, reject) => { contour.resolvers.push(resolve); this.reject = reject; }); }
  draw() {} destroy() { this.reject?.(new Error('contour request canceled')); }
} }));
vi.mock('../../pkg/nuclear_option_map.js', () => ({ default: core.init, MapViewport: class {
  resize() {} free() { core.free(); } zoom() { return 1; } scale() { return core.scale; } zoomAt(factor: number) { core.scale *= factor; } centerX() { return 0; } centerZ() { return 0; }
  worldToScreen(x: number, z: number) { return [x, -z]; } screenToWorld(x: number, y: number) { return [x, -y]; }
} }));
import { MapViewer, chooseContourInterval, adaptiveContourInterval, createMapViewer } from '../../src/viewer';

function surfaces(failObserve = false) {
  const frames = new Map<number, FrameRequestCallback>();
  let frameId = 0;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  const ctx = { setTransform: vi.fn(), fillRect: vi.fn(), drawImage: vi.fn() };
  const listeners = new Set<EventListener>();
  const windowListeners = new Set<EventListener>();
  const disconnect = vi.fn();
  vi.stubGlobal('ResizeObserver', class {
    observe() { if (failObserve) throw new Error('observe failed'); }
    disconnect() { disconnect(); }
  });
  vi.stubGlobal('window', {
    devicePixelRatio: 2,
    addEventListener: (_: string, fn: EventListener) => windowListeners.add(fn),
    removeEventListener: (_: string, fn: EventListener) => windowListeners.delete(fn),
  });
  let tabIndex: string | null = null;
  const canvas = {
    style: { touchAction: 'pan-x' }, tabIndex: -1, width: 0, height: 0,
    getContext: () => ctx,
    getAttribute: () => tabIndex,
    setAttribute: (_: string, value: string) => { tabIndex = value; },
    removeAttribute: () => { tabIndex = null; },
    addEventListener: (_: string, fn: EventListener) => listeners.add(fn),
    removeEventListener: (_: string, fn: EventListener) => listeners.delete(fn),
    getBoundingClientRect: () => ({ width: 300, height: 200 }),
  } as unknown as HTMLCanvasElement;
  const flushFrame = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(0)); };
  return { canvas, listeners, windowListeners, disconnect, frames, ctx, flushFrame };
}
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); contour.fail = false; contour.waitInterval = null; contour.resolvers = []; core.scale = 1; });
describe('viewer construction lifecycle', () => {
  it('coalesces input bursts into one paint and cancels queued paints on disposal', async () => {
    const surface = surfaces();
    const viewer = await createMapViewer(surface.canvas);
    surface.flushFrame(); surface.ctx.fillRect.mockClear();
    for (let i = 0; i < 30; i++) viewer.zoomIn();
    expect(viewer.state.scale).toBeCloseTo(1.5 ** 30);
    expect(surface.frames.size).toBe(1);
    expect(surface.ctx.fillRect).not.toHaveBeenCalled();
    surface.flushFrame();
    expect(surface.ctx.fillRect).toHaveBeenCalledOnce();
    viewer.zoomOut(); viewer.destroy(); surface.flushFrame();
    expect(surface.frames.size).toBe(0);
    expect(surface.ctx.fillRect).toHaveBeenCalledOnce();
  });
  it('retains the successful interval across map changes and temporary fallbacks', async () => {
    const surface = surfaces();
    vi.stubGlobal('Image', class { naturalWidth = 10; naturalHeight = 10; onload?: () => void; set src(_value: string) { queueMicrotask(() => this.onload?.()); } });
    const map = { id: 'a', name: 'A', widthMeters: 1000, heightMeters: 1000, image: 'a.png', grid: { columns: 1, rows: 1, offsetX: 0, offsetY: 0 }, contours: { intervals: [20, 50, 100] as (20 | 50 | 100)[], levels: [{ level: 0, columns: 1, rows: 1, urlTemplate: 'https://test/{interval}/{x}/{y}' }] } };
    const viewer = await createMapViewer(surface.canvas, { mode: 'raw', grid: false, map, contourInterval: 100 });
    expect(viewer.state.contourInterval).toBe(100);
    await viewer.setContourInterval(20);
    await viewer.setMap({ ...map, contours: { ...map.contours, intervals: [50] } }); expect(viewer.state.contourInterval).toBe(50);
    await viewer.setMap(map); expect(viewer.state.contourInterval).toBe(20);
    contour.fail = true; await expect(viewer.setContourInterval(50)).rejects.toThrow('load failed'); contour.fail = false;
    await viewer.setMap(map); expect(viewer.state.contourInterval).toBe(20);
    viewer.destroy();
  });
  it('preserves contour preferences and chooses a supported fallback', () => {
    expect(chooseContourInterval([20, 50, 100], 20)).toBe(20);
    expect(chooseContourInterval([50, 100], 20)).toBe(100);
    expect(chooseContourInterval([50], 20)).toBe(50);
    expect(chooseContourInterval([20, 50, 100], 20)).toBe(20);
    expect(() => chooseContourInterval([20, 50, 100], 30 as never)).toThrow('Contour interval');
  });
  it('uses progressively finer measured contours at closer scales and supports partial datasets', () => {
    expect(adaptiveContourInterval([10, 20, 50, 100], 1 / 120)).toBe(100);
    expect(adaptiveContourInterval([10, 20, 50, 100], 1 / 40)).toBe(50);
    expect(adaptiveContourInterval([10, 20, 50, 100], 1 / 15)).toBe(20);
    expect(adaptiveContourInterval([10, 20, 50, 100], 1 / 3)).toBe(10);
    expect(adaptiveContourInterval([50, 100], 1 / 3)).toBe(50);
    expect(() => adaptiveContourInterval([10], 0)).toThrow('scale');
  });
  it('cancels a pending finer interval when zoom reverses to the currently displayed interval', async () => {
    const surface = surfaces();
    vi.stubGlobal('Image', class { naturalWidth = 10; naturalHeight = 10; onload?: () => void; set src(_value: string) { queueMicrotask(() => this.onload?.()); } });
    const map = { id: 'a', name: 'A', widthMeters: 100000, heightMeters: 100000, image: 'a.png', grid: { columns: 10, rows: 10, offsetX: 0, offsetY: 0 }, contours: { intervals: [10, 20, 50, 100] as (10 | 20 | 50 | 100)[], levels: [{ level: 0, columns: 1, rows: 1, urlTemplate: 'https://test/{interval}/{x}/{y}' }] } };
    core.scale = 1 / 120;
    const viewer = await createMapViewer(surface.canvas, { mode: 'raw', grid: false, map });
    expect(viewer.state.contourInterval).toBe(100);
    contour.waitInterval = 20;
    core.scale = 1 / 30; viewer.zoomIn();
    expect(contour.resolvers).toHaveLength(1);
    core.scale = 1.5 / 120; viewer.zoomOut();
    contour.resolvers[0](); await Promise.resolve(); await Promise.resolve();
    expect(viewer.state.contourInterval).toBe(100);
    expect(viewer.state.contourPreference).toBe('auto');
    viewer.destroy();
  });
  it('keeps automatic mode when zoom cancels the first request made while enabling it', async () => {
    const surface = surfaces();
    vi.stubGlobal('Image', class { naturalWidth = 10; naturalHeight = 10; onload?: () => void; set src(_value: string) { queueMicrotask(() => this.onload?.()); } });
    const map = { id: 'a', name: 'A', widthMeters: 100000, heightMeters: 100000, image: 'a.png', grid: { columns: 10, rows: 10, offsetX: 0, offsetY: 0 }, contours: { intervals: [10, 20, 50, 100] as (10 | 20 | 50 | 100)[], levels: [{ level: 0, columns: 1, rows: 1, urlTemplate: 'https://test/{interval}/{x}/{y}' }] } };
    core.scale = 1 / 120;
    const viewer = await createMapViewer(surface.canvas, { mode: 'raw', grid: false, map, contourInterval: 20 });
    contour.waitInterval = 100;
    const enabling = viewer.setContourInterval('auto');
    expect(contour.resolvers).toHaveLength(1);
    core.scale = 1 / 30; viewer.zoomIn();
    await expect(enabling).resolves.toBeUndefined();
    expect(viewer.state.contourPreference).toBe('auto');
    expect(viewer.state.contourInterval).toBe(20);
    contour.waitInterval = null;
    core.scale = 1.5 / 120; viewer.zoomOut();
    await Promise.resolve(); await Promise.resolve();
    expect(viewer.state.contourPreference).toBe('auto');
    expect(viewer.state.contourInterval).toBe(100); viewer.destroy();
  });
  it('exposes automatic intervals and bounds smoothing without altering the map data', async () => {
    const surface = surfaces();
    const viewer = await createMapViewer(surface.canvas);
    expect(viewer.state.contourPreference).toBe('auto');
    expect(viewer.state.contourSmoothingMeters).toBe(40);
    viewer.setContourSmoothing(0); expect(viewer.state.contourSmoothingMeters).toBe(0);
    expect(() => viewer.setContourSmoothing(NaN)).toThrow('smoothing');
    expect(() => viewer.setContourSmoothing(161)).toThrow('smoothing');
    viewer.destroy();
  });
  it('keeps the latest manual preference when a map change cancels its old pending request', async () => {
    const surface = surfaces();
    vi.stubGlobal('Image', class { naturalWidth = 10; naturalHeight = 10; onload?: () => void; set src(_value: string) { queueMicrotask(() => this.onload?.()); } });
    const map = { id: 'a', name: 'A', widthMeters: 100000, heightMeters: 100000, image: 'a.png', grid: { columns: 10, rows: 10, offsetX: 0, offsetY: 0 }, contours: { intervals: [20, 50, 100] as (20 | 50 | 100)[], levels: [{ level: 0, columns: 1, rows: 1, urlTemplate: 'https://test/{interval}/{x}/{y}' }] } };
    core.scale = 1 / 120;
    const viewer = await createMapViewer(surface.canvas, { mode: 'raw', grid: false, map });
    contour.waitInterval = 20;
    const pending = viewer.setContourInterval(20);
    contour.waitInterval = null;
    await viewer.setMap({ ...map, id: 'b', name: 'B' }); await pending;
    expect(viewer.state.contourPreference).toBe(20);
    expect(viewer.state.contourInterval).toBe(20);
    expect(viewer.state.map?.id).toBe('b'); viewer.destroy();
  });
  it('rejects unsupported configured intervals before acquiring DOM resources', async () => {
    const surface = surfaces();
    await expect(createMapViewer(surface.canvas, { contourInterval: 30 as never })).rejects.toThrow('Contour interval');
    expect(surface.listeners.size).toBe(0);
    const viewer = await createMapViewer(surface.canvas);
    await expect(viewer.setContourInterval(20)).rejects.toThrow('unavailable'); viewer.destroy();
  });
  it('rejects direct JavaScript construction before accessing canvas or WASM', () => {
    const Direct = MapViewer as unknown as new (...args: unknown[]) => MapViewer;
    expect(() => new Direct(null, {})).toThrow('createMapViewer');
  });
  it('cleans every acquired resource when the initial consumer callback throws', async () => {
    const surface = surfaces();
    await expect(createMapViewer(surface.canvas, { onViewChange: () => { throw new Error('consumer callback'); } })).rejects.toThrow('consumer callback');
    expect(surface.listeners.size).toBe(0);
    expect(surface.windowListeners.size).toBe(0);
    expect(surface.disconnect).toHaveBeenCalledOnce();
    expect(core.free).toHaveBeenCalledOnce();
    expect(surface.canvas.style.touchAction).toBe('pan-x');
  });
  it('cleans partially installed listeners when observer setup fails', async () => {
    const surface = surfaces(true);
    await expect(createMapViewer(surface.canvas)).rejects.toThrow('observe failed');
    expect(surface.listeners.size).toBe(0);
    expect(surface.disconnect).toHaveBeenCalledOnce();
    expect(core.free).toHaveBeenCalledOnce();
    expect(surface.canvas.style.touchAction).toBe('pan-x');
  });
  it('creates and destroys through the async factory with idempotent cleanup', async () => {
    const surface = surfaces(), changed = vi.fn();
    const viewer = await createMapViewer(surface.canvas, { onViewChange: changed });
    expect(changed).toHaveBeenCalledOnce();
    expect(surface.canvas.width).toBe(600);
    expect(viewer.state.gridLabels).toBe('edges');
    viewer.setGridLabels('cells'); expect(viewer.state.gridLabels).toBe('cells');
    viewer.setGridLabels('none'); expect(viewer.state.gridLabels).toBe('none');
    expect(() => viewer.setGridLabels('invalid' as never)).toThrow('grid label');
    viewer.destroy(); viewer.destroy();
    expect(surface.listeners.size).toBe(0);
    expect(surface.windowListeners.size).toBe(0);
    expect(core.free).toHaveBeenCalledOnce();
  });
  it('rejects invalid initial label modes before installing DOM resources', async () => {
    const surface = surfaces();
    await expect(createMapViewer(surface.canvas, { gridLabels: 'invalid' as never })).rejects.toThrow('grid label');
    expect(surface.listeners.size).toBe(0);
    expect(surface.canvas.style.touchAction).toBe('pan-x');
  });
});
