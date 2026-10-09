import { describe, expect, it } from 'vite-plus/test';
import { gridCell, gridReference, gridRowLabel, validateMap, validateMapCatalog } from '../../src/catalog';
const map = { id: 'test', name: 'Test', widthMeters: 40000, heightMeters: 30000, image: 'island.png', grid: { columns: 8, rows: 6, offsetX: 0, offsetY: 0 } };
describe('map catalog contracts', () => {
  it('formats alphabetic rows beyond Z', () => {
    expect([0, 25, 26, 51, 52, 701, 702].map(gridRowLabel)).toEqual(['A', 'Z', 'AA', 'AZ', 'BA', 'ZZ', 'AAA']);
    for (const invalid of [-1, 1.5, NaN, Infinity]) expect(() => gridRowLabel(invalid)).toThrow();
  });
  it('returns references only within both image and declared grid bounds', () => {
    const shifted = { ...map, widthMeters: 100000, heightMeters: 100000, grid: { columns: 8, rows: 40, offsetX: 45000, offsetY: 45000 } };
    expect(gridReference(shifted, 0, 0)).toBe('E4');
    expect(gridReference(shifted, -45000, 45000)).toBe('A0');
    expect(gridReference(shifted, -45001, 45000)).toBeNull();
    expect(gridReference(shifted, 35000, 0)).toBeNull();
    expect(gridReference(shifted, 0, -50001)).toBeNull();
    expect(gridReference(shifted, Infinity, 0)).toBeNull();
    const tall = { ...shifted, heightMeters: 800000 };
    expect(gridReference(tall, 0, 45000 - 260000)).toBe('AA4');
  });
  it('uses world-meter grid origins and fixed 10 km game cells, independent of image extent', () => {
    const shifted = { ...map, widthMeters: 45000, grid: { ...map.grid, offsetX: 22000, offsetY: 18000 } };
    expect(gridCell(shifted, 0, 0)).toEqual({ column: 2, row: 1 });
    expect(gridCell(shifted, -22000, 18000)).toEqual({ column: 0, row: 0 });
    expect(gridCell(shifted, -22001, 18001)).toEqual({ column: -1, row: -1 });
  });
  it('resolves image references against the manifest URL', () => {
    expect(validateMapCatalog({ schemaVersion: 1, maps: [map] }, 'https://example.org/maps/catalog.json').maps[0].image).toBe('https://example.org/maps/island.png');
  });
  it('rejects duplicate IDs and unsupported schemas', () => {
    expect(() => validateMapCatalog({ schemaVersion: 1, maps: [map, map] }, 'https://example.org/')).toThrow('Duplicate');
    expect(() => validateMapCatalog({ schemaVersion: 2, maps: [] }, 'https://example.org/')).toThrow('schema');
  });
  it('rejects invalid dimensions and grid metadata', () => {
    for (const widthMeters of [0, -1, Infinity, NaN]) expect(() => validateMap({ ...map, widthMeters })).toThrow();
    expect(() => validateMap({ ...map, grid: { ...map.grid, columns: 1.5 } })).toThrow();
    expect(() => validateMap({ ...map, grid: { ...map.grid, offsetY: NaN } })).toThrow();
    expect(() => validateMap({ ...map, tint: 'green' })).toThrow();
  });
  it('returns independent grid metadata', () => {
    const copy = validateMap(map); copy.grid.columns = 20; expect(map.grid.columns).toBe(8);
  });
  it('validates and independently resolves lossless ground-height pyramids', () => {
    const topography = { minHeightMeters: 0, maxHeightMeters: 1000, heightStepMeters: .1, tiles: { tileSize: 256, gutter: 2, levels: [{ level: 0, width: 256, height: 256, columns: 1, rows: 1, urlTemplate: 'heights/{x}/{y}.png?v=abcd' }] } };
    const result = validateMapCatalog({ schemaVersion: 1, maps: [{ ...map, topography }] }, 'https://example.org/maps/manifest.json').maps[0];
    expect(result.topography?.tiles.levels[0].urlTemplate).toBe('https://example.org/maps/heights/{x}/{y}.png?v=abcd');
    expect(topography.tiles.levels[0].urlTemplate).toBe('heights/{x}/{y}.png?v=abcd');
    for (const heightStepMeters of [0, NaN, .001]) expect(() => validateMap({ ...map, topography: { ...topography, heightStepMeters } })).toThrow('height encoding');
    expect(() => validateMap({ ...map, topography: { ...topography, tiles: { ...topography.tiles, levels: [] } } })).toThrow('pyramid');
    expect(() => validateMapCatalog({ schemaVersion: 1, maps: [{ ...map, topography: { ...topography, tiles: { ...topography.tiles, levels: [{ ...topography.tiles.levels[0], urlTemplate: 'javascript:{x}/{y}' }] } } }] }, 'https://example.org/')).toThrow('topography URL');
  });
});
