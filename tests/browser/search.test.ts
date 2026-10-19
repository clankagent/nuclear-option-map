import { describe, expect, it } from 'vite-plus/test';
import { searchLocations } from '../../demo/ui/search';
const map = { id: 'map', name: 'Map', image: 'map.png', widthMeters: 100000, heightMeters: 100000, grid: { columns: 10, rows: 10, offsetX: 50000, offsetY: 50000 } };
describe('jump search', () => {
  it('combines grid, meter and case-insensitive pin matches', () => {
    const pins = [{ id: 'one', name: 'North field', x: 40, z: 60, color: 'green' as const }];
    expect(searchLocations('e4', map, pins)[0]).toMatchObject({ label: 'Grid E4', x: -5000, z: 5000 });
    expect(searchLocations('12000,-4500', map, pins)[0]).toMatchObject({ x: 12000, z: -4500 });
    expect(searchLocations('NORTH', map, pins)[0]).toMatchObject({ label: 'North field', pinId: 'one' });
    expect(searchLocations('outside', map, pins)).toEqual([]);
  });
});
