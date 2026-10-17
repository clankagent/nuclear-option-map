import { expect, it } from 'vite-plus/test';
import { parseWaterPreview, preferPublishedWater } from '../../demo/water-preview';
const empty = { committedCount: 0, scopeCount: null, remainingCount: null, coverage: [] };
const snapshot = { schemaVersion: 1, kind: 'incremental-water-preview', snapshotSha256: 'a'.repeat(64), productionAccepted: false, maps: { heartland: empty, ignus: { level: 10, width: 1280, height: 640, committedCount: 2, scopeCount: 554202, remainingCount: 554200, coverage: [{ x: 120, y: 1, columns: 2, rows: 1 }], urlTemplate: '/maps/candidate-v8/incremental/ignus/10/{x}/{y}.webp' } } };
it('requires exact partial scope and keeps accepted coarse imagery and finest overrides', () => {
  const row = parseWaterPreview(snapshot);
  const coarse = { level: 9, width: 327680, height: 163840, columns: 640, rows: 320, urlTemplate: 'old/{x}/{y}' };
  const finest = { level: 10, width: 655360, height: 327680, columns: 1280, rows: 640, urlTemplate: 'old-finest/{x}/{y}', urlOverrides: { '120/1': 'accepted-tree.webp' } };
  const pyramid = { tileSize: 512, gutter: 2, levels: [coarse, finest] };
  const result = preferPublishedWater(pyramid, row.maps.ignus);
  expect(result.levels[0]).toBe(coarse);
  expect(result.levels[1].urlOverrides).toBe(finest.urlOverrides);
  expect(result.levels[1].preferred!.coverage).toEqual(row.maps.ignus.coverage);
  expect(pyramid.levels[1]).not.toHaveProperty('preferred');
  expect(preferPublishedWater(pyramid, row.maps.heartland)).toBe(pyramid);
});
it('rejects invented completion, foreign routes, mismatched counts and out-of-bounds coverage', () => {
  for (const patch of [{ productionAccepted: true }, { maps: { ...snapshot.maps, ignus: { ...snapshot.maps.ignus, committedCount: 3 } } }, { maps: { ...snapshot.maps, ignus: { ...snapshot.maps.ignus, urlTemplate: 'http://elsewhere/{x}/{y}' } } }, { maps: { ...snapshot.maps, ignus: { ...snapshot.maps.ignus, coverage: [{ x: 1280, y: 0, columns: 2, rows: 1 }] } } }]) expect(() => parseWaterPreview({ ...snapshot, ...patch })).toThrow();
});
