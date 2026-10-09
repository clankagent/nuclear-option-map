import { afterEach, expect, it, vi } from 'vite-plus/test';
import { PyramidRenderer, type TileImage, type RasterView } from '../../src/pyramid';
import { validateMapCatalog } from '../../src/catalog';
const pyramid = { tileSize: 512, levels: [0, 1, 2].map(level => ({ level, width: 512 * 2 ** level, height: 512 * 2 ** level, columns: 2 ** level, rows: 2 ** level, urlTemplate: `https://test/${level}/{x}/{y}` })) };
const view: RasterView = { x: 0, y: 0, width: 512, height: 512, screenWidth: 512, screenHeight: 512, dpr: 1 };
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
function fixture(budget?: number) {
  let now = 0;
  const requests: { url: string; signal: AbortSignal; resolve: (image: TileImage) => void; reject: (e: Error) => void }[] = [];
  const redraw = vi.fn();
  const renderer = new PyramidRenderer(pyramid, (url, signal) => new Promise((resolve, reject) => requests.push({ url, signal, resolve, reject })), redraw, budget, () => now);
  const image = () => ({ image: {} as CanvasImageSource, width: 512, height: 512, dispose: vi.fn() });
  vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1)); vi.stubGlobal('cancelAnimationFrame', vi.fn());
  const ctx = { save: vi.fn(), restore: vi.fn(), drawImage: vi.fn(), globalAlpha: 1 } as unknown as CanvasRenderingContext2D;
  return { renderer, requests, redraw, image, ctx, tick: (value: number) => { now = value; } };
}
afterEach(() => vi.unstubAllGlobals());
it('accounts for physical gutters but draws the logical tile bounds', async () => {
  const f = fixture(), image = { ...f.image(), width: 516, height: 516 };
  let now = 1000;
  const renderer = new PyramidRenderer({ ...pyramid, gutter: 2 }, async () => image, vi.fn(), undefined, () => now);
  renderer.update(view); await flush(); now = 1200; renderer.draw(f.ctx, view);
  expect(renderer.stats.bytes).toBe(516 * 516 * 4);
  expect(f.ctx.drawImage).toHaveBeenCalledWith(image.image, 2, 2, 512, 512, 0, 0, 512, 512);
  renderer.destroy();
});
it('retains coarse coverage while delayed detail loads and fades new detail above it', async () => {
  const f = fixture(); f.renderer.update(view); const parent = f.image(); f.requests[0].resolve(parent); await flush(); f.tick(200);
  f.renderer.update({ ...view, width: 1024, height: 1024 });
  expect(f.renderer.stats.pending).toBeGreaterThan(0);
  f.renderer.draw(f.ctx, view); expect(f.ctx.drawImage).toHaveBeenCalledWith(parent.image, 0, 0, 512, 512, 0, 0, 512, 512);
  const child = f.image(); f.requests[1].resolve(child); await flush();
  f.renderer.draw(f.ctx, view); expect(f.ctx.drawImage).not.toHaveBeenCalledWith(child.image, 0, 0, 512, 512, 0, 0, 256, 256); expect(requestAnimationFrame).toHaveBeenCalled();
  f.tick(290); f.renderer.draw(f.ctx, view); expect(f.ctx.globalAlpha).toBe(.5);
  f.renderer.destroy(); expect(parent.dispose).toHaveBeenCalledOnce(); expect(child.dispose).toHaveBeenCalledOnce();
});
it('limits concurrent loads, removes stale queued tiles and disposes late completions after destruction', async () => {
  const f = fixture(); f.renderer.update({ ...view, width: 2048, height: 2048, screenWidth: 2048, screenHeight: 2048 });
  expect(f.requests).toHaveLength(6);
  f.renderer.update({ ...view, x: 10000 });
  expect(f.renderer.stats.pending).toBe(0); expect(f.requests.every(r => r.signal.aborted)).toBe(true);
  f.renderer.destroy(); expect(f.requests.every(r => r.signal.aborted)).toBe(true);
  const late = f.image(); f.requests[3].resolve(late); await flush(); expect(late.dispose).toHaveBeenCalledOnce(); expect(f.redraw).not.toHaveBeenCalled();
});
it('failed child requests leave loaded parent available and do not retry every frame', async () => {
  const f = fixture(); f.renderer.update(view); const parent = f.image(); f.requests[0].resolve(parent); await flush(); f.tick(200);
  f.renderer.update({ ...view, width: 1024, height: 1024 }); f.requests[1].reject(new Error('404')); await flush();
  const count = f.requests.length; f.renderer.update({ ...view, width: 1024, height: 1024 }); expect(f.requests).toHaveLength(count);
  f.renderer.draw(f.ctx, view); expect(f.ctx.drawImage).toHaveBeenCalled(); expect(parent.dispose).not.toHaveBeenCalled();
  f.renderer.destroy();
});
it('retries visible transient errors with backoff and stops after three attempts', async () => {
  const f = fixture(); f.renderer.update(view); f.requests[0].reject(new Error('temporary')); await flush();
  f.tick(499); f.renderer.update(view); expect(f.requests).toHaveLength(1);
  f.tick(500); f.renderer.update(view); expect(f.requests).toHaveLength(2); f.requests[1].reject(new Error('temporary')); await flush();
  f.tick(1499); f.renderer.update(view); expect(f.requests).toHaveLength(2);
  f.tick(1500); f.renderer.update(view); expect(f.requests).toHaveLength(3); f.requests[2].reject(new Error('persistent')); await flush();
  f.tick(10000); f.renderer.update(view); expect(f.requests).toHaveLength(3); f.renderer.destroy();
});
it('retains finer children while zoom-out parent loads then smoothly withdraws them', async () => {
  const f = fixture(); f.renderer.update({ ...view, width: 1024, height: 1024 }); const child = f.image(); f.requests[0].resolve(child); await flush(); f.tick(200);
  f.renderer.update({ ...view, width: 400, height: 400 }); f.renderer.draw(f.ctx, view); expect(child.dispose).not.toHaveBeenCalled();
  const parentRequest = f.requests.find(r => r.url === 'https://test/0/0/0')!; parentRequest.resolve(f.image()); await flush();
  f.tick(290); f.renderer.draw(f.ctx, view); expect(f.ctx.globalAlpha).toBe(.5);
  f.tick(400); (f.ctx.drawImage as ReturnType<typeof vi.fn>).mockClear(); f.renderer.draw(f.ctx, view);
  expect((f.ctx.drawImage as ReturnType<typeof vi.fn>).mock.calls.some(call => call[0] === child.image)).toBe(false);
  f.renderer.destroy();
});
it('evicts offscreen decoded tiles by byte budget', async () => {
  const f = fixture(); const images: TileImage[] = [];
  const wide = { tileSize: 512, levels: [{ level: 0, width: 2048, height: 512, columns: 4, rows: 1, urlTemplate: 'https://test/{x}/{y}' }] };
  const renderer = new PyramidRenderer(wide, async () => { const image = f.image(); images.push(image); return image; }, vi.fn(), 1024 * 1024, () => 1000);
  renderer.update({ ...view, width: 2048, screenWidth: 200 }); await flush();
  expect(renderer.stats.bytes).toBeLessThanOrEqual(1024 * 1024);
  expect(images.some(image => (image.dispose as ReturnType<typeof vi.fn>).mock.calls.length > 0)).toBe(true);
  renderer.destroy();
});
it('validates pyramid dimensions and resolves templates without escaping placeholders', () => {
  const map = { id: 'a', name: 'A', image: 'overview.png', widthMeters: 1000, heightMeters: 1000, grid: { columns: 1, rows: 1, offsetX: 0, offsetY: 0 }, tiles: { ...pyramid, levels: pyramid.levels.map(l => ({ ...l, urlTemplate: 'tiles/{z}/{x}/{y}.webp' })) } };
  expect(validateMapCatalog({ schemaVersion: 1, maps: [map] }, 'https://test/maps/catalog.json').maps[0].tiles?.levels[0].urlTemplate).toBe('https://test/maps/tiles/{z}/{x}/{y}.webp');
  expect(() => validateMapCatalog({ schemaVersion: 1, maps: [{ ...map, tiles: { ...pyramid, levels: [{ ...pyramid.levels[0], columns: 2 }] } }] }, 'https://test/')).toThrow('level');
});
