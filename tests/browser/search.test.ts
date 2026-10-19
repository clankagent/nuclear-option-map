import { describe, expect, it } from 'vite-plus/test';
import { searchLocations } from '../../demo/ui/search';
const map = { id: 'map', name: 'Map', image: 'map.png', widthMeters: 100000, heightMeters: 100000, grid: { columns: 10, rows: 10, offsetX: 50000, offsetY: 50000 } };
describe('jump search', () => {
  it('resolves grid references and meter coordinates', () => {
    expect(searchLocations('e4', map)[0]).toMatchObject({ label: 'Grid E4', x: -5000, z: 5000 });
    expect(searchLocations('12000,-4500', map)[0]).toMatchObject({ x: 12000, z: -4500 });
    expect(searchLocations('outside', map)).toEqual([]);
  });
});
