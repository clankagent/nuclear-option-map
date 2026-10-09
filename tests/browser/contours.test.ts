import { afterEach, expect, it, vi } from 'vite-plus/test';
import { ContourRenderer, contourLabelStations, decodeContours, smoothContourPoints, reduceContourChunk, CONTOUR_FRAME_PRIMITIVES } from '../../src/contours';
const data = { schemaVersion: 1, bounds: [-500, -500, 500, 500], paths: [{ id: 'height100', elevationMeters: 100, points: [[-400, 0], [0, 0], [400, 0]] }] };
afterEach(() => vi.unstubAllGlobals());
it('decodes measured world coordinates to typed arrays without inventing elevations', () => {
  const decoded = decodeContours(data); expect([...decoded.paths[0].points]).toEqual([-400, 0, 0, 0, 400, 0]); expect(decoded.paths[0].elevationMeters).toBe(100);
  expect(() => decodeContours({ ...data, paths: [{ ...data.paths[0], points: [[NaN, 0], [1, 1]] }] })).toThrow('coordinates');
  expect(() => decodeContours({ ...data, bounds: [0, 0, -1, -1] })).toThrow('tile');
});
it('pins rectangular root coverage and aborts pending tile work on destruction', async () => {
  const fetcher = vi.fn(async (url: string, options: { signal: AbortSignal }) => ({ ok: true, json: async () => ({ ...data, bounds: url.endsWith('/0/0') ? [-500, -500, 0, 500] : [0, -500, 500, 500] }) })); vi.stubGlobal('fetch', fetcher);
  const renderer = new ContourRenderer({ intervals: [20], levels: [{ level: 0, columns: 2, rows: 1, urlTemplate: 'https://test/{interval}/{z}/{x}/{y}' }] }, 20, 1000, 1000, vi.fn());
  await renderer.prepare(); expect(fetcher.mock.calls.map(c => c[0])).toEqual(['https://test/20/0/0/0', 'https://test/20/0/1/0']); renderer.destroy();
});
it('draws constant-pixel contour strokes and readable index elevation labels', async () => {
  vi.stubGlobal('fetch', async () => ({ ok: true, json: async () => data })); vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1)); vi.stubGlobal('cancelAnimationFrame', vi.fn());
  const renderer = new ContourRenderer({ intervals: [20], levels: [{ level: 0, columns: 1, rows: 1, urlTemplate: 'https://test/{interval}/{x}/{y}' }] }, 20, 1000, 1000, vi.fn()); await renderer.prepare();
  const widths: number[] = [], context = { lineWidth: 0, save() {}, restore() {}, beginPath() {}, rect() {}, clip() {}, moveTo() {}, lineTo() {}, stroke() { widths.push(this.lineWidth); }, measureText: () => ({ width: 32 }), translate() {}, rotate: vi.fn(), strokeText: vi.fn(), fillText: vi.fn(), drawImage: vi.fn(), setTransform() {}, clearRect() {}, globalCompositeOperation: 'source-over' };
  vi.stubGlobal('document', { createElement: () => ({ width: 0, height: 0, getContext: () => context }) });
  renderer.draw(context as unknown as CanvasRenderingContext2D, { width: 1000, height: 1000, scale: 1, worldToScreen: (x, z) => ({ x: x + 500, y: 500 - z }), screenToWorld: (x, y) => ({ x: x - 500, z: 500 - y }) });
  expect(widths).toContain(1.1); expect(context.fillText).toHaveBeenCalledWith('100 m', 0, 0); expect(context.fillText.mock.calls.length).toBeLessThanOrEqual(3); renderer.destroy();
});
it('places evenly spaced labels along long segments with upright angles', () => {
  const stations = contourLabelStations([{ x: 0, y: 0 }, { x: 1500, y: 0 }]);
  expect(stations.map(p => p.x)).toEqual([180, 500, 820, 1140, 1460]);
  const diagonal = contourLabelStations([{ x: 1000, y: 1000 }, { x: 0, y: 0 }]);
  expect(diagonal.every(p => p.angle >= -Math.PI / 2 && p.angle <= Math.PI / 2)).toBe(true);
});
it('jumps to visible stations on a deep-zoom path spanning billions of offscreen pixels', () => {
  const stations = contourLabelStations([{ x: -1e9, y: 100 }, { x: 1e9, y: 100 }], 320, [0, 0, 1000, 500]);
  expect(stations.length).toBeGreaterThan(0); expect(stations.length).toBeLessThanOrEqual(4);
  expect(stations.every(p => p.x >= 0 && p.x <= 1000 && p.y === 100)).toBe(true);
  expect(contourLabelStations([{ x: -1e9, y: -1000 }, { x: 1e9, y: -1000 }], 320, [0, 0, 1000, 500])).toEqual([]);
});
it('rejects invalid spacing instead of hanging and bounds pathological candidate counts', () => {
  for (const spacing of [0, -1, NaN, Infinity, -Infinity]) expect(() => contourLabelStations([{ x: 0, y: 0 }, { x: 1000, y: 0 }], spacing)).toThrow('spacing');
  expect(contourLabelStations([{ x: 0, y: 0 }, { x: 1e9, y: 0 }], 1)).toHaveLength(4096);
});
it('rejects mismatched ownership bounds', async () => {
  vi.stubGlobal('fetch', async () => ({ ok: true, json: async () => ({ ...data, bounds: [-1, -1, 1, 1] }) }));
  const renderer = new ContourRenderer({ intervals: [20], levels: [{ level: 0, columns: 1, rows: 1, urlTemplate: 'https://test/{interval}/{x}/{y}' }] }, 20, 1000, 1000, vi.fn());
  await expect(renderer.prepare()).rejects.toThrow('bounds'); renderer.destroy();
});
it('aborts sibling root loads immediately when preparing one root fails', async () => {
  const signals: AbortSignal[] = [];
  vi.stubGlobal('fetch', (url: string, options: { signal: AbortSignal }) => { signals.push(options.signal); return url.endsWith('/0/0') ? Promise.reject(new Error('missing root')) : new Promise(() => {}); });
  const renderer = new ContourRenderer({ intervals: [20], levels: [{ level: 0, columns: 2, rows: 1, urlTemplate: 'https://test/{interval}/{x}/{y}' }] }, 20, 1000, 1000, vi.fn());
  await expect(renderer.prepare()).rejects.toThrow('missing root'); expect(signals[1].aborted).toBe(true);
});

it('rounds measured corners locally while preserving raw vertices and endpoints', () => {
  const raw = new Float64Array([-100, 0, 0, 0, 0, 100]);
  expect(smoothContourPoints(raw, 0)).toBe(raw);
  const rounded = smoothContourPoints(raw, 24);
  expect([...raw]).toEqual([-100, 0, 0, 0, 0, 100]);
  expect([...rounded.slice(0, 2)]).toEqual([-100, 0]);
  expect([...rounded.slice(-2)]).toEqual([0, 100]);
  for (let i = 0; i < rounded.length; i += 2) {
    expect(rounded[i]).toBeLessThanOrEqual(0); expect(rounded[i + 1]).toBeGreaterThanOrEqual(0);
    expect(Math.min(Math.abs(rounded[i]), Math.abs(rounded[i + 1]))).toBeLessThanOrEqual(12);
  }
  expect(() => smoothContourPoints(raw, NaN)).toThrow('smoothing');
});

it('rounds the closure of a complete contour ring without a seam kink', () => {
  const ring = new Float64Array([0, 0, 100, 0, 100, 100, 0, 100, 0, 0]);
  const rounded = smoothContourPoints(ring, 20);
  expect([...rounded.slice(0, 2)]).toEqual([0, 20]);
  expect([...rounded.slice(-2)]).toEqual([0, 20]);
  for (let i = 0; i < rounded.length; i += 2) {
    expect(rounded[i]).toBeGreaterThanOrEqual(0); expect(rounded[i]).toBeLessThanOrEqual(100);
    expect(rounded[i + 1]).toBeGreaterThanOrEqual(0); expect(rounded[i + 1]).toBeLessThanOrEqual(100);
  }
});

it('culls offscreen root fallback paths before issuing strokes at close zoom', async () => {
  vi.stubGlobal('fetch', async () => ({ ok: true, json: async () => ({ ...data, paths: [...data.paths, { id: 'offscreen', elevationMeters: 120, points: [[300, 300], [350, 350], [400, 300]] }] }) }));
  vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1)); vi.stubGlobal('cancelAnimationFrame', vi.fn());
  const renderer = new ContourRenderer({ intervals: [20], levels: [{ level: 0, columns: 1, rows: 1, urlTemplate: 'https://test/{interval}/{x}/{y}' }] }, 20, 1000, 1000, vi.fn()); await renderer.prepare();
  const context = { save() {}, restore() {}, beginPath() {}, rect() {}, clip() {}, moveTo() {}, lineTo() {}, stroke: vi.fn(), measureText: () => ({ width: 32 }), translate() {}, rotate() {}, strokeText() {}, fillText() {}, drawImage() {}, setTransform() {}, clearRect() {} };
  vi.stubGlobal('document', { createElement: () => ({ width: 0, height: 0, getContext: () => context }) });
  renderer.draw(context as unknown as CanvasRenderingContext2D, { width: 100, height: 100, scale: 1, worldToScreen: (x, z) => ({ x: x + 50, y: 50 - z }), screenToWorld: (x, y) => ({ x: x - 50, z: 50 - y }) });
  expect(context.stroke).toHaveBeenCalledTimes(1); renderer.destroy();
});

function performanceContext() {
  let pathCommands = 0, maxPathCommands = 0;
  const ctx = { save() {}, restore() {}, beginPath() { pathCommands = 0; }, rect() {}, clip() {}, moveTo() {}, lineTo: vi.fn(() => { maxPathCommands = Math.max(maxPathCommands, ++pathCommands); }), quadraticCurveTo: vi.fn(() => { maxPathCommands = Math.max(maxPathCommands, ++pathCommands); }), stroke: vi.fn(), measureText: () => ({ width: 32 }), translate() {}, rotate() {}, strokeText() {}, fillText() {}, drawImage() {}, setTransform() {}, clearRect() {} };
  vi.stubGlobal('document', { createElement: () => ({ width: 0, height: 0, getContext: () => ctx }) });
  vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1)); vi.stubGlobal('cancelAnimationFrame', vi.fn());
  return { ctx, maxPathCommands: () => maxPathCommands };
}
it('clips a 500k-vertex coarse fallback shared by four visible child regions before transforms', async () => {
  const points = Array.from({ length: 500000 }, (_, i) => [i - 250000, i % 2 ? .5 : -.5]);
  vi.stubGlobal('fetch', async (url: string) => url.startsWith('https://test/0/') ? ({ ok: true, json: async () => ({ schemaVersion: 1, bounds: [-500000, -500000, 500000, 500000], paths: [{ id: 'large-native', elevationMeters: 100, points }] }) }) : new Promise(() => {}));
  const renderer = new ContourRenderer({ intervals: [20], levels: Array.from({ length: 4 }, (_, level) => ({ level, columns: 2 ** level, rows: 2 ** level, urlTemplate: `https://test/${level}/{x}/{y}` })) }, 20, 1000000, 1000000, vi.fn());
  await renderer.prepare(); const surface = performanceContext(), worldToScreen = vi.fn((x: number, z: number) => ({ x: x + 50, y: 50 - z }));
  const transform = { width: 100, height: 100, scale: 1, worldToScreen, screenToWorld: (x: number, y: number) => ({ x: x - 50, z: 50 - y }) };
  for (let frame = 0; frame < 20; frame++) { renderer.draw(surface.ctx as unknown as CanvasRenderingContext2D, transform); if (!renderer.stats.pending) break; }
  expect(renderer.stats.pending).toBe(false);
  expect(surface.ctx.lineTo.mock.calls.length + surface.ctx.quadraticCurveTo.mock.calls.length).toBeLessThan(1000);
  expect(worldToScreen.mock.calls.length).toBeLessThan(100);
  expect(surface.maxPathCommands()).toBeLessThanOrEqual(256); renderer.destroy();
}, 30000);
it('finishes every visible segment over bounded frames without constructing million-command paths', async () => {
  const points = Array.from({ length: 50000 }, (_, i) => [i % 1000 - 500, i % 2 ? 400 : -400]);
  vi.stubGlobal('fetch', async () => ({ ok: true, json: async () => ({ ...data, paths: [{ id: 'dense-native', elevationMeters: 100, points }] }) }));
  const renderer = new ContourRenderer({ intervals: [20], levels: [{ level: 0, columns: 1, rows: 1, urlTemplate: 'https://test/root' }] }, 20, 1000, 1000, vi.fn()); renderer.smoothingMeters = 0;
  await renderer.prepare(); const surface = performanceContext();
  vi.stubGlobal('performance', { now: () => 0 });
  const transform = { width: 1000, height: 1000, scale: 1, worldToScreen: (x: number, z: number) => ({ x: x + 500, y: 500 - z }), screenToWorld: (x: number, y: number) => ({ x: x - 500, z: 500 - y }) };
  let frames = 0;
  do { renderer.draw(surface.ctx as unknown as CanvasRenderingContext2D, transform); expect(renderer.stats.lastFramePrimitives).toBeLessThanOrEqual(CONTOUR_FRAME_PRIMITIVES); frames++; } while (renderer.stats.pending && frames < 100);
  expect(frames).toBeGreaterThan(1); expect(renderer.stats.pending).toBe(false);
  expect(surface.ctx.lineTo).toHaveBeenCalledTimes(points.length - 1);
  expect(surface.maxPathCommands()).toBeLessThanOrEqual(256); renderer.destroy();
}, 30000);
it('bounds screen-level chunk reduction while retaining source vertices and endpoints', () => {
  const points = new Float64Array(Array.from({ length: 65 }, (_, i) => [i, Math.sin(i / 10) * .2]).flat());
  const before = points.slice(), selected = reduceContourChunk(points, 0, 64, .25);
  expect(selected).toEqual([0, 64]); expect(points).toEqual(before);
  for (let i = 1; i < 64; i++) {
    const a = selected[0], b = selected[1], dx = points[b * 2] - points[a * 2], dz = points[b * 2 + 1] - points[a * 2 + 1], x = points[i * 2] - points[a * 2], z = points[i * 2 + 1] - points[a * 2 + 1];
    expect(Math.abs(x * dz - z * dx) / Math.hypot(dx, dz)).toBeLessThanOrEqual(.25);
  }
});
it('cancels unfinished geometry on a pan and reuses its bounded scratch canvas', async () => {
  const points = Array.from({ length: 20000 }, (_, i) => [i % 1000 - 500, i % 2 ? 400 : -400]);
  vi.stubGlobal('fetch', async () => ({ ok: true, json: async () => ({ ...data, paths: [{ id: 'dense-native', elevationMeters: 100, points }] }) }));
  const renderer = new ContourRenderer({ intervals: [20], levels: [{ level: 0, columns: 1, rows: 1, urlTemplate: 'https://test/root' }] }, 20, 1000, 1000, vi.fn()); renderer.smoothingMeters = 0;
  await renderer.prepare(); const surface = performanceContext(), createElement = vi.fn(() => ({ width: 0, height: 0, getContext: () => surface.ctx }));
  vi.stubGlobal('document', { createElement }); vi.stubGlobal('performance', { now: () => 0 });
  const view = { width: 1000, height: 1000, scale: 1, worldToScreen: (x: number, z: number) => ({ x: x + 500, y: 500 - z }), screenToWorld: (x: number, y: number) => ({ x: x - 500, z: 500 - y }) };
  renderer.draw(surface.ctx as unknown as CanvasRenderingContext2D, view); expect(renderer.stats.pending).toBe(true);
  const issued = surface.ctx.lineTo.mock.calls.length;
  const outside = { ...view, worldToScreen: (x: number, z: number) => ({ x: x - 10000, y: 500 - z }), screenToWorld: (x: number, y: number) => ({ x: x + 10000, z: 500 - y }) };
  renderer.draw(surface.ctx as unknown as CanvasRenderingContext2D, outside);
  expect(renderer.stats.pending).toBe(false); expect(surface.ctx.lineTo).toHaveBeenCalledTimes(issued);
  expect(createElement).toHaveBeenCalledTimes(1);
  renderer.draw(surface.ctx as unknown as CanvasRenderingContext2D, view); expect(renderer.stats.pending).toBe(true);
  expect(createElement).toHaveBeenCalledTimes(2); renderer.destroy();
}, 30000);
