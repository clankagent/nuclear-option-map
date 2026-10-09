import { afterEach, expect, it, vi } from 'vite-plus/test';
import { ContourRenderer, contourLabelStations, decodeContours } from '../../src/contours';
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
