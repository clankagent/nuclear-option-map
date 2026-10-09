import { expect, it } from 'vite-plus/test';
import { colorizeTopography, decodeHeight, topographyPalette } from '../../src/topo-style';
const descriptor = { tiles: { tileSize: 256, levels: [] }, minHeightMeters: -10, heightStepMeters: .1, maxHeightMeters: 1000 };
it('decodes big-endian uint16 measured height without modifying source samples', () => {
  expect(decodeHeight(1, 2, descriptor)).toBeCloseTo(15.8);
  expect(decodeHeight(255, 255, descriptor)).toBeCloseTo(6543.5);
});
it('masks water and unknown cells and leaves packed data unchanged', () => {
  const packed = new Uint8ClampedArray([0, 100, 180, 0, 0, 100, 180, 255]), original = packed.slice();
  const out = colorizeTopography(packed, topographyPalette(descriptor, { style: 'lowlands-dark', strength: .5 }), false);
  expect([...out.slice(0, 4)]).toEqual([0, 0, 0, 0]); expect(out[7]).toBe(128); expect(packed).toEqual(original);
});
it('uses darker lowlands, a restrained elevation gradient and neutral plain mode', () => {
  const low = topographyPalette(descriptor, { style: 'lowlands-dark', strength: 1 });
  expect(low[100 * 4]).toBeLessThan(low[10000 * 4]);
  const elevation = topographyPalette(descriptor, { style: 'elevation-colors', strength: 1 });
  expect(elevation[100 * 4 + 1]).toBeGreaterThan(elevation[100 * 4]);
  expect(elevation[10000 * 4]).toBeGreaterThan(245);
  expect(topographyPalette(descriptor, { style: 'plain', strength: 1 })[3]).toBe(0);
});
it('strength only controls opacity and relief preserves a neutral flat plane', () => {
  const a = topographyPalette(descriptor, { style: 'hillshade', strength: 1 }), b = topographyPalette(descriptor, { style: 'hillshade', strength: .2 });
  expect([...a.slice(0,3)]).toEqual([...b.slice(0,3)]); expect(b[3]).toBe(51);
  const packed = new Uint8ClampedArray([0, 10, 180, 255, 0, 10, 90, 255, 0, 10, 250, 255]);
  const out = colorizeTopography(packed, a, true);
  expect(out[0]).toBe(246); expect(out[4]).toBeLessThan(out[0]); expect(out[8]).toBeGreaterThan(out[0]);
});

it('reuses cached packed tiles across style and strength changes and bounds recoloring per frame', async () => {
  const { TopoStyleRenderer } = await import('../../src/topo-style');
  const { vi } = await import('vite-plus/test');
  const put = vi.fn();
  vi.stubGlobal('document', { createElement: () => ({ width: 0, height: 0, getContext: () => ({ drawImage: vi.fn(), setTransform: vi.fn(), clearRect: vi.fn(), save: vi.fn(), restore: vi.fn(), globalAlpha: 1, getImageData: () => ({ data: new Uint8ClampedArray(256 * 256 * 4).fill(255) }), putImageData: put }) }) });
  vi.stubGlobal('ImageData', class { constructor(public data: Uint8ClampedArray, public width: number, public height: number) {} });
  vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1)); vi.stubGlobal('cancelAnimationFrame', vi.fn());
  const loader = vi.fn(async () => ({ image: {} as CanvasImageSource, width: 256, height: 256, dispose: vi.fn() }));
  const topo = { ...descriptor, tiles: { tileSize: 256, levels: [{ level: 0, width: 1024, height: 256, columns: 4, rows: 1, urlTemplate: 'https://test/{x}/{y}' }] } };
  const renderer = new TopoStyleRenderer(topo, vi.fn(), 24 * 1024 * 1024, loader);
  const view = { x: 0, y: 0, width: 1024, height: 256, screenWidth: 1024, screenHeight: 256, dpr: 1 };
  renderer.update(view); expect(loader).not.toHaveBeenCalled();
  renderer.setStyle('lowlands-dark', .5); renderer.update(view);
  for (let i = 0; i < 15; i++) await Promise.resolve();
  expect(loader).toHaveBeenCalledTimes(4); expect(renderer.stats.bytes).toBe(4 * 256 * 256 * 8);
  const ctx = { save: vi.fn(), restore: vi.fn(), drawImage: vi.fn(), globalAlpha: 1 } as unknown as CanvasRenderingContext2D;
  await new Promise(resolve => setTimeout(resolve, 200));
  put.mockClear(); renderer.setStyle('elevation-colors', .2, true); renderer.draw(ctx, view);
  expect(put).toHaveBeenCalledTimes(2); renderer.draw(ctx, view);
  expect(put).toHaveBeenCalledTimes(4);
  put.mockClear(); renderer.setStyle('elevation-colors', .8, true); renderer.draw(ctx, view); expect(put).not.toHaveBeenCalled();
  expect(renderer.stats.bytes).toBe(4 * 256 * 256 * 8 + 1024 * 256 * 4);
  renderer.update(view); expect(loader).toHaveBeenCalledTimes(4); renderer.destroy(); vi.unstubAllGlobals();
});




it('disposes a decoded source arriving after destruction before readback or color work', async () => {
  const { TopoStyleRenderer } = await import('../../src/topo-style');
  const { vi } = await import('vite-plus/test');
  const createElement = vi.fn(() => { throw new Error('Late decode must not allocate a canvas'); });
  vi.stubGlobal('document', { createElement }); vi.stubGlobal('cancelAnimationFrame', vi.fn());
  let resolve!: (source: import('../../src/pyramid').TileImage) => void;
  const dispose = vi.fn();
  const topo = { ...descriptor, tiles: { tileSize: 256, levels: [{ level: 0, width: 256, height: 256, columns: 1, rows: 1, urlTemplate: 'https://test/{x}/{y}' }] } };
  const renderer = new TopoStyleRenderer(topo, vi.fn(), undefined, () => new Promise(r => { resolve = r; }));
  renderer.setStyle('lowlands-dark', .5); renderer.update({ x: 0, y: 0, width: 256, height: 256, screenWidth: 256, screenHeight: 256, dpr: 1 });
  renderer.destroy(); resolve({ image: {} as CanvasImageSource, width: 256, height: 256, dispose });
  for (let i = 0; i < 15; i++) await Promise.resolve();
  expect(dispose).toHaveBeenCalledOnce(); expect(createElement).not.toHaveBeenCalled(); vi.unstubAllGlobals();
});

it('caps geographic fill sampling at CSS density and reserves visible ancestor capacity', async () => {
  const { topographySamplingView } = await import('../../src/topo-style');
  const pyramid = { tileSize: 256, gutter: 2, levels: [0,1,2,3,4].map(level => ({ level, width: 512 * 2 ** level, height: 256 * 2 ** level, columns: 2 * 2 ** level, rows: 2 ** level, urlTemplate: 'https://test/{x}/{y}' })) };
  const view = { x: 0, y: 0, width: 1280, height: 640, screenWidth: 1280, screenHeight: 800, dpr: 2 };
  const sample = topographySamplingView(view, pyramid, 4 * 1024 * 1024);
  expect(sample.dpr).toBeLessThanOrEqual(1);
  const density = Math.max(sample.width,sample.height) * sample.dpr;
  const selected = pyramid.levels.find(l => Math.max(l.width,l.height) >= density)!;
  expect(selected.level).toBe(1); // whole-map detail + root fit, rather than retina tiles
  expect(topographySamplingView({ ...view, screenWidth: 1000000, screenHeight: 1000000 }, pyramid, 4 * 1024 * 1024).dpr).toBeLessThanOrEqual(1);
});

it('requires enough retained capacity for all root tiles before reserving scratch', async () => {
  const { TopoStyleRenderer } = await import('../../src/topo-style');
  const topo = { ...descriptor, tiles: { tileSize: 512, gutter: 8, levels: [{ level: 0, width: 1024, height: 1024, columns: 2, rows: 2, urlTemplate: 'https://test/{x}/{y}' }] } };
  expect(() => new TopoStyleRenderer(topo, () => {}, 8 * 1024 * 1024)).toThrow('budget');
  const renderer = new TopoStyleRenderer(topo, () => {});
  expect(renderer.stats.bytes).toBe(0); renderer.destroy();
});
