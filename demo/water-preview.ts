import type { TileCoverage, TilePyramid } from '../src/catalog';

export interface WaterPreviewMap { level?: number; width?: number; height?: number; committedCount: number; scopeCount: number | null; remainingCount: number | null; coverage: TileCoverage[]; urlTemplate?: string }
export interface WaterPreview { schemaVersion: 1; kind: 'incremental-water-preview'; snapshotSha256: string; productionAccepted: false; maps: { heartland: WaterPreviewMap; ignus: WaterPreviewMap } }
const hash = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);

export function parseWaterPreview(value: unknown): WaterPreview {
  const row = value as WaterPreview;
  if (!row || row.schemaVersion !== 1 || row.kind !== 'incremental-water-preview' || row.productionAccepted !== false || !hash(row.snapshotSha256) || !row.maps) throw new TypeError('Invalid partial water preview');
  for (const family of ['heartland', 'ignus'] as const) {
    const map = row.maps[family];
    if (!map || !Number.isSafeInteger(map.committedCount) || map.committedCount < 0 || !Array.isArray(map.coverage) || map.coverage.length > 4096) throw new TypeError('Invalid water coverage');
    if (map.committedCount === 0 && map.scopeCount === null && map.remainingCount === null && map.coverage.length === 0) continue;
    const width = family === 'ignus' ? 1280 : 640;
    if (map.level !== 10 || map.width !== width || map.height !== 640 || !Number.isSafeInteger(map.scopeCount) || map.scopeCount! > width * 640 || map.scopeCount! < map.committedCount || map.remainingCount !== map.scopeCount! - map.committedCount || map.urlTemplate !== `/maps/candidate-v8/incremental/${family}/10/{x}/{y}.webp`) throw new TypeError('Invalid water publication scope');
    let cells = 0;
    for (const r of map.coverage) {
      if (!r || ![r.x, r.y, r.columns, r.rows].every(Number.isSafeInteger) || r.x < 0 || r.y < 0 || r.columns < 1 || r.rows < 1 || r.x + r.columns > width || r.y + r.rows > 640) throw new TypeError('Invalid water coverage rectangle');
      cells += r.columns * r.rows;
    }
    if (cells !== map.committedCount) throw new TypeError('Water count does not match coverage');
  }
  return row;
}

/** Keep every baseline URL/override and all coarse levels. Prefer new verified detail. */
export function preferPublishedWater(pyramid: TilePyramid, map: WaterPreviewMap): TilePyramid {
  if (!map.committedCount) return pyramid;
  const finest = pyramid.levels.at(-1)!;
  if (finest.level !== map.level || finest.columns !== map.width || finest.rows !== map.height || pyramid.tileSize !== 512 || pyramid.gutter !== 2) throw new TypeError('Water and baseline lattices differ');
  return { ...pyramid, levels: pyramid.levels.map(level => level === finest ? { ...level, preferred: { urlTemplate: map.urlTemplate!, coverage: map.coverage } } : level) };
}
