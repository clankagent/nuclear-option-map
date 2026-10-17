import type { TopographyPyramid } from './topo-style';
/** Tile coordinates, with exclusive right/bottom edges. Rectangles may overlap. */
export interface TileCoverage { x: number; y: number; columns: number; rows: number }
export interface PyramidLevel { level: number; width: number; height: number; columns: number; rows: number; urlTemplate: string; coverage?: TileCoverage[]; /** Exact x/y URLs for changed tiles; other coordinates use urlTemplate. */ urlOverrides?: Record<string, string>; /** Verified replacements take priority over baseline overrides. */ preferred?: { urlTemplate: string; coverage: TileCoverage[] } }
export interface TilePyramid { tileSize: number; gutter?: number; levels: PyramidLevel[] }
/** Missing coverage means a complete level; an empty array means no tiles. */
export function tileAvailable(level: PyramidLevel, x: number, y: number): boolean {
  return x >= 0 && y >= 0 && x < level.columns && y < level.rows &&
    (baselineTileAvailable(level, x, y) || preferredTileAvailable(level, x, y));
}
export function baselineTileAvailable(level: PyramidLevel, x: number, y: number): boolean {
  return x >= 0 && y >= 0 && x < level.columns && y < level.rows && (level.coverage === undefined || level.coverage.some(r => x >= r.x && y >= r.y && x < r.x + r.columns && y < r.y + r.rows));
}
export function preferredTileAvailable(level: PyramidLevel, x: number, y: number): boolean {
  return !!level.preferred?.coverage.some(r => x >= r.x && y >= r.y && x < r.x + r.columns && y < r.y + r.rows);
}
export type ContourInterval = 10 | 20 | 50 | 100;
export interface ContourPyramid { intervals: ContourInterval[]; levels: { level: number; columns: number; rows: number; urlTemplate: string }[] }
export interface MapDefinition {
  id: string;
  name: string;
  widthMeters: number;
  heightMeters: number;
  image: string;
  /** Game grid counts and world-meter offsets. Cells are 10 km; x=-offsetX+column*10000, z=offsetY-row*10000. */
  grid: { columns: number; rows: number; offsetX: number; offsetY: number };
  tint?: string;
  tiles?: TilePyramid;
  contours?: ContourPyramid;
  topography?: TopographyPyramid;
}
export interface MapCatalog { schemaVersion: 1; maps: MapDefinition[] }
export function gridCell(map: MapDefinition, x: number, z: number) {
  return { column: Math.floor((map.grid.offsetX + x) / 10000), row: Math.floor((map.grid.offsetY - z) / 10000) };
}
/** Alphabetic row label: zero is A, 25 is Z, 26 is AA. */
export function gridRowLabel(row: number): string {
  if (!Number.isSafeInteger(row) || row < 0) throw new TypeError('Grid row must be a nonnegative safe integer');
  let label = '', n = row + 1;
  while (n > 0) { n--; label = String.fromCharCode(65 + n % 26) + label; n = Math.floor(n / 26); }
  return label;
}
/** Returns a reference only within both the map image and declared game grid. */
export function gridReference(map: MapDefinition, x: number, z: number): string | null {
  if (!Number.isFinite(x) || !Number.isFinite(z) || Math.abs(x) > map.widthMeters / 2 || Math.abs(z) > map.heightMeters / 2) return null;
  const { column, row } = gridCell(map, x, z);
  return column >= 0 && column < map.grid.columns && row >= 0 && row < map.grid.rows ? `${gridRowLabel(row)}${column}` : null;
}
export function validTint(value: unknown): value is string {
  return typeof value === 'string' && /^#[\da-f]{6}$/i.test(value);
}
export function validateMap(value: unknown): MapDefinition {
  if (!value || typeof value !== 'object') throw new TypeError('Map must be an object');
  const m = value as MapDefinition;
  for (const key of ['id', 'name', 'image'] as const) {
    if (typeof m[key] !== 'string' || !m[key].trim()) throw new TypeError(`Map ${key} must be a nonempty string`);
  }
  for (const key of ['widthMeters', 'heightMeters'] as const) {
    if (!Number.isFinite(m[key]) || m[key] <= 0) throw new TypeError(`Map ${key} must be positive and finite`);
  }
  if (!m.grid || typeof m.grid !== 'object') throw new TypeError('Map grid is required');
  for (const key of ['columns', 'rows'] as const) {
    if (!Number.isSafeInteger(m.grid[key]) || m.grid[key] < 1 || m.grid[key] > 10000) throw new TypeError(`Grid ${key} must be an integer from 1 to 10000`);
  }
  for (const key of ['offsetX', 'offsetY'] as const) {
    if (!Number.isFinite(m.grid[key])) throw new TypeError(`Grid ${key} must be finite`);
  }
  if (m.tint !== undefined && !validTint(m.tint)) throw new TypeError('Tint must be #RRGGBB');
  let tiles: TilePyramid | undefined;
  if (m.tiles !== undefined) {
    const p = m.tiles;
    if (!p || !Number.isSafeInteger(p.tileSize) || p.tileSize < 64 || p.tileSize > 2048 || !Array.isArray(p.levels) || !p.levels.length || p.levels.length > 20) throw new TypeError('Invalid tile pyramid');
    if (p.gutter !== undefined && (!Number.isSafeInteger(p.gutter) || p.gutter < 0 || p.gutter > 8 || p.tileSize + p.gutter * 2 > 2048)) throw new TypeError('Invalid tile gutter');
    let overrideCount = 0;
    const levels = p.levels.map((l, index) => {
      if (!l || l.level !== index || !Number.isSafeInteger(l.width) || !Number.isSafeInteger(l.height) || l.width < 1 || l.height < 1 || l.width > 1048576 || l.height > 1048576 || l.columns !== Math.ceil(l.width / p.tileSize) || l.rows !== Math.ceil(l.height / p.tileSize) || typeof l.urlTemplate !== 'string' || !l.urlTemplate.includes('{x}') || !l.urlTemplate.includes('{y}')) throw new TypeError('Invalid pyramid level');
      if (index && (Math.ceil(l.width / 2) !== p.levels[index - 1].width || Math.ceil(l.height / 2) !== p.levels[index - 1].height)) throw new TypeError('Pyramid levels must double their dimensions');
      if (l.coverage !== undefined) {
        if (index === 0 || !Array.isArray(l.coverage) || l.coverage.length > 4096) throw new TypeError('Invalid pyramid coverage; root must be complete');
        for (const r of l.coverage) {
          if (!r || ![r.x, r.y, r.columns, r.rows].every(Number.isSafeInteger) || r.x < 0 || r.y < 0 || r.columns < 1 || r.rows < 1 || r.x + r.columns > l.columns || r.y + r.rows > l.rows) throw new TypeError('Invalid pyramid coverage rectangle');
        }
      }
      if (l.preferred !== undefined) {
        const preferred = l.preferred;
        if (!preferred || typeof preferred.urlTemplate !== 'string' || !preferred.urlTemplate.includes('{x}') || !preferred.urlTemplate.includes('{y}') || !Array.isArray(preferred.coverage) || preferred.coverage.length > 4096) throw new TypeError('Invalid preferred tile coverage');
        for (const r of preferred.coverage) if (!r || ![r.x, r.y, r.columns, r.rows].every(Number.isSafeInteger) || r.x < 0 || r.y < 0 || r.columns < 1 || r.rows < 1 || r.x + r.columns > l.columns || r.y + r.rows > l.rows) throw new TypeError('Invalid preferred tile coverage rectangle');
      }
      let urlOverrides: Record<string, string> | undefined;
      if (l.urlOverrides !== undefined) {
        if (!l.urlOverrides || typeof l.urlOverrides !== 'object' || Array.isArray(l.urlOverrides) || ![Object.prototype, null].includes(Object.getPrototypeOf(l.urlOverrides))) throw new TypeError('Invalid tile URL overrides');
        const entries = Object.entries(l.urlOverrides);
        overrideCount += entries.length;
        if (overrideCount > 100000) throw new TypeError('Tile URL overrides exceed 100000 entries per pyramid');
        for (const [key, url] of entries) {
          if (!/^(0|[1-9]\d*)\/(0|[1-9]\d*)$/.test(key)) throw new TypeError('Invalid tile URL override coordinate');
          const [x, y] = key.split('/').map(Number);
          if (!Number.isSafeInteger(x) || !Number.isSafeInteger(y) || !tileAvailable(l, x, y)) throw new TypeError('Tile URL override is outside declared coverage');
          if (typeof url !== 'string' || !url.trim()) throw new TypeError('Invalid tile URL override URL');
        }
        urlOverrides = Object.fromEntries(entries);
      }
      return { ...l, ...(l.coverage !== undefined ? { coverage: l.coverage.map(r => ({ ...r })) } : {}), ...(urlOverrides ? { urlOverrides } : {}), ...(l.preferred ? { preferred: { ...l.preferred, coverage: l.preferred.coverage.map(r => ({ ...r })) } } : {}) };
    });
    tiles = { tileSize: p.tileSize, gutter: p.gutter ?? 0, levels };
  }
  let contours: ContourPyramid | undefined;
  if (m.contours !== undefined) {
    const c = m.contours;
    if (!c || !Array.isArray(c.intervals) || !c.intervals.length || c.intervals.some(i => ![10, 20, 50, 100].includes(i)) || new Set(c.intervals).size !== c.intervals.length || !Array.isArray(c.levels) || !c.levels.length || c.levels.length > 12) throw new TypeError('Invalid contour pyramid');
    const levels = c.levels.map((l, i) => {
      if (!l || l.level !== i || !Number.isSafeInteger(l.columns) || !Number.isSafeInteger(l.rows) || l.columns < 1 || l.rows < 1 || l.columns > 8192 || l.rows > 8192 || (i > 0 && (l.columns !== c.levels[i - 1].columns * 2 || l.rows !== c.levels[i - 1].rows * 2)) || typeof l.urlTemplate !== 'string' || !['{interval}', '{x}', '{y}'].every(p => l.urlTemplate.includes(p))) throw new TypeError('Invalid contour level');
      return { ...l };
    });
    if (levels[0].columns * levels[0].rows > 4) throw new TypeError('Contour root must contain at most four tiles');
    contours = { intervals: [...c.intervals], levels };
  }
  let topography: TopographyPyramid | undefined;
  if (m.topography !== undefined) {
    const p = m.topography;
    if (!p || !Number.isFinite(p.minHeightMeters) || !Number.isFinite(p.maxHeightMeters) || p.maxHeightMeters <= p.minHeightMeters || !Number.isFinite(p.heightStepMeters) || p.heightStepMeters <= 0 || p.heightStepMeters > 1000 || (p.maxHeightMeters - p.minHeightMeters) / p.heightStepMeters > 65535) throw new TypeError('Invalid topography height encoding');
    // Share the same strict pyramid validation as ordinary raster layers.
    const validated = validateMap({ ...m, topography: undefined, tiles: p.tiles });
    if (!validated.tiles || validated.tiles.tileSize > 512) throw new TypeError('Topography tiles must use a tile size of at most 512 pixels');
    if (validated.tiles.levels[0].columns * validated.tiles.levels[0].rows > 4) throw new TypeError('Topography root must contain at most four tiles');
    topography = { ...p, tiles: validated.tiles };
  }
  return { ...m, grid: { ...m.grid }, ...(tiles ? { tiles } : {}), ...(contours ? { contours } : {}), ...(topography ? { topography } : {}) };
}
function resolveTileOverrides(level: PyramidLevel, baseUrl: string) {
  if (level.preferred) {
    const url = new URL(level.preferred.urlTemplate, baseUrl);
    if (!['http:', 'https:', 'file:'].includes(url.protocol)) throw new TypeError('Unsupported preferred tile URL');
    level.preferred.urlTemplate = url.href.replace(/%7B/gi, '{').replace(/%7D/gi, '}');
  }
  if (!level.urlOverrides) return;
  for (const [key, value] of Object.entries(level.urlOverrides)) {
    const url = new URL(value, baseUrl);
    if (!['http:', 'https:', 'file:'].includes(url.protocol)) throw new TypeError('Unsupported tile URL override');
    level.urlOverrides[key] = url.href;
  }
}
export function validateMapCatalog(value: unknown, baseUrl: string): MapCatalog {
  const c = value as MapCatalog;
  if (!c || c.schemaVersion !== 1 || !Array.isArray(c.maps)) throw new TypeError('Expected a schema 1 map catalog');
  const ids = new Set<string>();
  const maps = c.maps.map(value => {
    const map = validateMap(value);
    if (ids.has(map.id)) throw new TypeError(`Duplicate map id: ${map.id}`);
    ids.add(map.id);
    map.image = new URL(map.image, baseUrl).href;
    if (!['http:', 'https:', 'file:', 'blob:', 'data:'].includes(new URL(map.image).protocol)) throw new TypeError('Unsupported map image URL');
    if (map.tiles) for (const level of map.tiles.levels) {
      const resolved = new URL(level.urlTemplate, baseUrl);
      if (!['http:', 'https:', 'file:'].includes(resolved.protocol)) throw new TypeError('Unsupported tile URL');
      level.urlTemplate = resolved.href.replace(/%7B/gi, '{').replace(/%7D/gi, '}');
      resolveTileOverrides(level, baseUrl);
    }
    if (map.contours) for (const level of map.contours.levels) {
      const resolved = new URL(level.urlTemplate, baseUrl);
      if (!['http:', 'https:', 'file:'].includes(resolved.protocol)) throw new TypeError('Unsupported contour URL');
      level.urlTemplate = resolved.href.replace(/%7B/gi, '{').replace(/%7D/gi, '}');
    }
    if (map.topography) for (const level of map.topography.tiles.levels) {
      const resolved = new URL(level.urlTemplate, baseUrl);
      if (!['http:', 'https:', 'file:'].includes(resolved.protocol)) throw new TypeError('Unsupported topography URL');
      level.urlTemplate = resolved.href.replace(/%7B/gi, '{').replace(/%7D/gi, '}');
      resolveTileOverrides(level, baseUrl);
    }
    return map;
  });
  return { schemaVersion: 1, maps };
}
export async function loadMapCatalog(url: string | URL): Promise<MapCatalog> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Map catalog request failed (${response.status})`);
  return validateMapCatalog(await response.json(), response.url || String(url));
}
