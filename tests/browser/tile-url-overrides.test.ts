import { describe, expect, it, vi } from 'vite-plus/test';
import { validateMap, validateMapCatalog, type MapDefinition } from '../../src/catalog';
import { PyramidRenderer, type RasterView, type TileImage } from '../../src/pyramid';
const base = 'https://example.org/maps/catalog.json';
const map = (): MapDefinition => ({ id: 'test', name: 'Test', widthMeters: 1000, heightMeters: 1000, image: 'overview.webp', grid: { columns: 1, rows: 1, offsetX: 0, offsetY: 0 }, tiles: { tileSize: 512, levels: [0, 1].map(level => ({ level, width: 512 * 2 ** level, height: 512 * 2 ** level, columns: 2 ** level, rows: 2 ** level, urlTemplate: `baseline/${level}/{x}/{y}.webp` })) } });
const catalog = (m: MapDefinition) => validateMapCatalog({ schemaVersion: 1, maps: [m] }, base).maps[0];
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

describe('changed tile URL overrides', () => {
  it('copies and resolves changed URLs without changing baseline templates or source objects', () => {
    const source = map(); source.tiles!.levels[1].urlOverrides = { '1/0': '../changed/1.webp?v=123', '0/1': 'file:///C:/tiles/other.webp' };
    const copy = validateMap(source); copy.tiles!.levels[1].urlOverrides!['1/0'] = 'different.webp';
    expect(source.tiles!.levels[1].urlOverrides!['1/0']).toBe('../changed/1.webp?v=123');
    const result = catalog(source);
    expect(result.tiles!.levels[1].urlOverrides).toEqual({ '1/0': 'https://example.org/changed/1.webp?v=123', '0/1': 'file:///C:/tiles/other.webp' });
    expect(result.tiles!.levels[1].urlTemplate).toBe('https://example.org/maps/baseline/1/{x}/{y}.webp');
    expect(source.tiles!.levels[1].urlOverrides!['1/0']).toBe('../changed/1.webp?v=123');
  });
  it('rejects noncanonical coordinates, out-of-bounds coordinates and absent coverage', () => {
    for (const key of ['01/0', '-1/0', '1.0/0', '1/0/0', '1e0/0', '0/+1', '2/0', '0/2', '9007199254740992/0']) {
      const source = map(); source.tiles!.levels[1].urlOverrides = { [key]: 'changed.webp' };
      expect(() => validateMap(source)).toThrow('override');
    }
    const source = map(); source.tiles!.levels[1].coverage = [{ x: 1, y: 0, columns: 1, rows: 2 }];
    source.tiles!.levels[1].urlOverrides = { '0/0': 'changed.webp' };
    expect(() => validateMap(source)).toThrow('coverage');
    source.tiles!.levels[1].urlOverrides = { '1/0': 'changed.webp' };
    expect(catalog(source).tiles!.levels[1].urlOverrides!['1/0']).toBe('https://example.org/maps/changed.webp');
  });
  it('rejects malformed maps, empty URLs, unsupported protocols and excessive entries', () => {
    for (const value of [null, [], 'bad', new Date(), { '0/0': '' }, { '0/0': '  ' }, { '0/0': 42 }]) {
      const source = map(); source.tiles!.levels[0].urlOverrides = value as never;
      expect(() => validateMap(source)).toThrow('override');
    }
    for (const url of ['javascript:alert(1)', 'data:image/png;base64,AA', 'blob:https://example.org/id', 'ftp://example.org/a']) {
      const source = map(); source.tiles!.levels[0].urlOverrides = { '0/0': url };
      expect(() => catalog(source)).toThrow('Unsupported tile URL override');
    }
    const source = map(); source.tiles = { tileSize: 64, levels: [{ level: 0, width: 65536, height: 65536, columns: 1024, rows: 1024, urlTemplate: '{x}/{y}', urlOverrides: Object.fromEntries(Array.from({ length: 100001 }, (_, i) => [`${i % 1024}/${Math.floor(i / 1024)}`, 'changed.webp'])) }] };
    expect(() => validateMap(source)).toThrow('100000');
  });
  it('resolves overrides for the shared topography pyramid contract', () => {
    const source = map(); source.topography = { minHeightMeters: 0, maxHeightMeters: 1000, heightStepMeters: 1, tiles: source.tiles! };
    source.tiles!.levels[0].urlOverrides = { '0/0': 'changed-height.png' };
    expect(catalog(source).topography!.tiles.levels[0].urlOverrides!['0/0']).toBe('https://example.org/maps/changed-height.png');
  });
  it('requests exact changed URLs alongside baseline tiles, caches coordinates and isolates a new candidate', async () => {
    const source = map(); source.tiles = { tileSize: 512, levels: [{ level: 0, width: 1024, height: 512, columns: 2, rows: 1, urlTemplate: 'baseline/{z}/{x}/{y}.webp', urlOverrides: { '0/0': 'changed-v1/0.webp' } }] };
    const image = (): TileImage => ({ image: {} as CanvasImageSource, width: 512, height: 512, dispose: vi.fn() });
    const load = vi.fn(async (_url: string) => image()), renderer = new PyramidRenderer(catalog(source).tiles!, load, vi.fn());
    const view: RasterView = { x: 0, y: 0, width: 1024, height: 512, screenWidth: 1024, screenHeight: 512, dpr: 1 };
    renderer.update(view); await flush();
    expect(load.mock.calls.map(call => call[0])).toEqual(['https://example.org/maps/changed-v1/0.webp', 'https://example.org/maps/baseline/0/1/0.webp']);
    renderer.update(view); await flush(); expect(load).toHaveBeenCalledTimes(2); renderer.destroy();
    source.tiles.levels[0].urlOverrides = { '0/0': 'changed-v2/0.webp' };
    const nextLoad = vi.fn(async (_url: string) => image()), next = new PyramidRenderer(catalog(source).tiles!, nextLoad, vi.fn());
    next.update(view); await flush();
    expect(nextLoad.mock.calls.map(call => call[0])).toContain('https://example.org/maps/changed-v2/0.webp');
    next.destroy();
  });
});
