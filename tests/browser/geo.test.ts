import { describe, expect, it } from 'vite-plus/test';
import { gridReference } from '../../src/catalog';
import { bearingDegrees, cardinal, distanceMeters, formatBearing, formatCoordinate, formatDistance, formatDuration, knotsToMetersPerSecond, MACH_1_KNOTS, niceScaleBar, parseLocation, travelTimeSeconds } from '../../demo/ui/geo';

const map = { id: 'test', name: 'Test', image: 'test.png', widthMeters: 100000, heightMeters: 100000, grid: { columns: 10, rows: 10, offsetX: 50000, offsetY: 50000 } };

describe('map geometry and formatting', () => {
  it('measures world meters and true bearings clockwise from north', () => {
    const origin = { x: 0, z: 0 };
    expect(distanceMeters(origin, { x: 3000, z: 4000 })).toBe(5000);
    expect(bearingDegrees(origin, { x: 0, z: 1 })).toBe(0);
    expect(bearingDegrees(origin, { x: 1, z: 0 })).toBe(90);
    expect(bearingDegrees(origin, { x: -1, z: 0 })).toBe(270);
    expect(cardinal(22.5)).toBe('NNE');
    expect(cardinal(360)).toBe('N');
    expect(formatBearing(45)).toBe('045° NE');
    expect(formatBearing(359.8)).toBe('000° N');
  });
  it('formats distances, coordinates, speeds and times without locale dependence', () => {
    expect(formatDistance(742, 'metric')).toBe('742 m');
    expect(formatDistance(.175, 'metric')).toBe('0.17 m');
    expect(formatDistance(.175, 'nautical')).toBe('0.17 m');
    expect(formatDistance(.001, 'metric')).toBe('<0.01 m');
    expect(formatDistance(0, 'metric')).toBe('0 m');
    expect(formatDistance(12400, 'metric')).toBe('12.4 km');
    expect(formatDistance(123456, 'metric')).toBe('123 km');
    expect(formatDistance(100, 'nautical')).toBe('100 m');
    expect(formatDistance(1852, 'nautical')).toBe('1.0 nm');
    expect(formatDistance(185200, 'nautical')).toBe('100 nm');
    expect(formatCoordinate(-4500.2)).toBe('-4,500');
    expect(knotsToMetersPerSecond(3600)).toBeCloseTo(1852);
    expect(MACH_1_KNOTS).toBe(661.5);
    expect(travelTimeSeconds(1852, 600)).toBe(6);
    expect(formatDuration(3930)).toBe('1h 05m');
    expect(formatDuration(750)).toBe('12m 30s');
    expect(formatDuration(45)).toBe('45s');
  });
  it('chooses a nice bar in either unit system', () => {
    for (const units of ['metric', 'nautical'] as const) {
      const bar = niceScaleBar(10, units);
      expect(bar.pixels).toBeGreaterThanOrEqual(80);
      expect(bar.pixels).toBeLessThanOrEqual(160);
      expect(bar.meters / 10).toBe(bar.pixels);
    }
    expect(niceScaleBar(10, 'metric')).toMatchObject({ meters: 1000, label: '1.0 km' });
    expect(() => niceScaleBar(0, 'metric')).toThrow('scale bar');
  });
  it('parses grid references with catalog row and column conventions', () => {
    const location = parseLocation('e4', map);
    expect(location).toEqual({ kind: 'grid', reference: 'E4', x: -5000, z: 5000, cell: { minX: -10000, maxX: 0, minZ: 0, maxZ: 10000 } });
    expect(gridReference(map, location!.x, location!.z)).toBe('E4');
    for (const [x, z] of [[-45000, 45000], [49999, -49999], [0, 0]]) {
      const reference = gridReference(map, x, z)!;
      const parsed = parseLocation(reference, map)!;
      expect(parsed.kind).toBe('grid');
      expect(gridReference(map, parsed.x, parsed.z)).toBe(reference);
    }
    expect(parseLocation('K0', map)).toBeNull();
    expect(parseLocation('A10', map)).toBeNull();
  });
  it('parses meter pairs and rejects points outside the map', () => {
    for (const input of ['x 12000 z -4500', 'x=12000, z=-4500', '12000,-4500', '12000 -4500']) expect(parseLocation(input, map)).toEqual({ kind: 'point', x: 12000, z: -4500 });
    expect(parseLocation('50001,0', map)).toBeNull();
    expect(parseLocation('1e999,0', map)).toBeNull();
    expect(parseLocation('nonsense', map)).toBeNull();
  });
});
