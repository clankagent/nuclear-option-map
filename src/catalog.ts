export interface MapDefinition {
  id: string;
  name: string;
  widthMeters: number;
  heightMeters: number;
  image: string;
  /** Game grid counts and world-meter offsets. Cells are 10 km; x=-offsetX+column*10000, z=offsetY-row*10000. */
  grid: { columns: number; rows: number; offsetX: number; offsetY: number };
  tint?: string;
}
export interface MapCatalog { schemaVersion: 1; maps: MapDefinition[] }
export function gridCell(map: MapDefinition, x: number, z: number) {
  return { column: Math.floor((map.grid.offsetX + x) / 10000), row: Math.floor((map.grid.offsetY - z) / 10000) };
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
  return { ...m, grid: { ...m.grid } };
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
    return map;
  });
  return { schemaVersion: 1, maps };
}
export async function loadMapCatalog(url: string | URL): Promise<MapCatalog> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Map catalog request failed (${response.status})`);
  return validateMapCatalog(await response.json(), response.url || String(url));
}
