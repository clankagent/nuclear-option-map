import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
const core = vi.hoisted(() => ({ init: vi.fn(async () => undefined), free: vi.fn() }));
const contour = vi.hoisted(() => ({ fail: false }));
vi.mock('../../src/contours', () => ({ ContourRenderer: class {
  constructor(_p: unknown, readonly interval: number) {}
  async prepare() { if (contour.fail) throw new Error('contour load failed'); }
  draw() {} destroy() {}
} }));
vi.mock('../../pkg/nuclear_option_map.js', () => ({ default: core.init, MapViewport: class {
  resize() {} free() { core.free(); } zoom() { return 1; } scale() { return 1; } centerX() { return 0; } centerZ() { return 0; }
  worldToScreen(x: number, z: number) { return [x, -z]; } screenToWorld(x: number, y: number) { return [x, -y]; }
} }));
import { MapViewer, chooseContourInterval, createMapViewer } from '../../src/viewer';

function surfaces(failObserve = false) {
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
    getContext: () => ({ setTransform() {}, fillRect() {}, drawImage() {} }),
    getAttribute: () => tabIndex,
    setAttribute: (_: string, value: string) => { tabIndex = value; },
    removeAttribute: () => { tabIndex = null; },
    addEventListener: (_: string, fn: EventListener) => listeners.add(fn),
    removeEventListener: (_: string, fn: EventListener) => listeners.delete(fn),
    getBoundingClientRect: () => ({ width: 300, height: 200 }),
  } as unknown as HTMLCanvasElement;
  return { canvas, listeners, windowListeners, disconnect };
}
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); contour.fail = false; });
describe('viewer construction lifecycle', () => {
  it('retains the successful interval across map changes and temporary fallbacks', async () => {
    const surface = surfaces();
    vi.stubGlobal('Image', class { naturalWidth = 10; naturalHeight = 10; onload?: () => void; set src(_value: string) { queueMicrotask(() => this.onload?.()); } });
    const map = { id: 'a', name: 'A', widthMeters: 1000, heightMeters: 1000, image: 'a.png', grid: { columns: 1, rows: 1, offsetX: 0, offsetY: 0 }, contours: { intervals: [20, 50, 100] as (20 | 50 | 100)[], levels: [{ level: 0, columns: 1, rows: 1, urlTemplate: 'https://test/{interval}/{x}/{y}' }] } };
    const viewer = await createMapViewer(surface.canvas, { mode: 'raw', grid: false, map });
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
