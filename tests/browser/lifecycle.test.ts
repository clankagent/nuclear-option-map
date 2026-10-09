import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
const core = vi.hoisted(() => ({ init: vi.fn(async () => undefined), free: vi.fn() }));
vi.mock('../../pkg/nuclear_option_map.js', () => ({ default: core.init, MapViewport: class {
  resize() {} free() { core.free(); } zoom() { return 1; } scale() { return 1; } centerX() { return 0; } centerZ() { return 0; }
} }));
import { MapViewer, createMapViewer } from '../../src/viewer';

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
    getContext: () => ({ setTransform() {}, fillRect() {} }),
    getAttribute: () => tabIndex,
    setAttribute: (_: string, value: string) => { tabIndex = value; },
    removeAttribute: () => { tabIndex = null; },
    addEventListener: (_: string, fn: EventListener) => listeners.add(fn),
    removeEventListener: (_: string, fn: EventListener) => listeners.delete(fn),
    getBoundingClientRect: () => ({ width: 300, height: 200 }),
  } as unknown as HTMLCanvasElement;
  return { canvas, listeners, windowListeners, disconnect };
}
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });
describe('viewer construction lifecycle', () => {
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
