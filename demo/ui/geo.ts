import { gridRowLabel } from "../../src/catalog";

export type UnitSystem = "metric" | "nautical";
export type Point = { x: number; z: number };
type GridMap = {
  widthMeters: number;
  heightMeters: number;
  grid: { columns: number; rows: number; offsetX: number; offsetY: number };
};
export type ParsedLocation =
  | {
      kind: "grid";
      reference: string;
      x: number;
      z: number;
      cell: { minX: number; minZ: number; maxX: number; maxZ: number };
    }
  | { kind: "point"; x: number; z: number };

const NM_METERS = 1852;
const group = (value: string) => value.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
const fixed = (value: number, digits: number) => {
  const result = value.toFixed(digits);
  const [whole, fraction] = result.split(".");
  return group(whole) + (fraction === undefined ? "" : `.${fraction}`);
};
export function formatDistance(meters: number, units: UnitSystem): string {
  const magnitude = Math.abs(meters);
  if ((units === "metric" || units === "nautical") && magnitude > 0 && magnitude < 1)
    return magnitude < 0.01 ? "<0.01 m" : `${fixed(magnitude, 2)} m`;
  if (units === "metric")
    return magnitude < 1000
      ? `${fixed(magnitude, 0)} m`
      : `${fixed(magnitude / 1000, magnitude < 100000 ? 1 : 0)} km`;
  if (units === "nautical")
    return magnitude < NM_METERS / 10
      ? `${fixed(magnitude, 0)} m`
      : `${fixed(magnitude / NM_METERS, magnitude < NM_METERS * 100 ? 1 : 0)} nm`;
  throw new TypeError("Unknown unit system");
}
export function formatCoordinate(v: number): string {
  return fixed(Math.round(v), 0);
}
/** Choose a 1/2/5 scale in meters or nautical miles, favoring the center of the requested width. */
export function niceScaleBar(
  metersPerPixel: number,
  units: UnitSystem,
  minPx = 80,
  maxPx = 160,
): { meters: number; pixels: number; label: string } {
  if (
    !Number.isFinite(metersPerPixel) ||
    metersPerPixel <= 0 ||
    !Number.isFinite(minPx) ||
    !Number.isFinite(maxPx) ||
    minPx <= 0 ||
    maxPx < minPx
  )
    throw new TypeError("Invalid scale bar range");
  const base = units === "nautical" ? NM_METERS : units === "metric" ? 1 : NaN;
  if (!Number.isFinite(base)) throw new TypeError("Unknown unit system");
  const target = (metersPerPixel * (minPx + maxPx)) / 2 / base;
  const exponent = Math.floor(Math.log10(target));
  let best: { meters: number; pixels: number; label: string } | null = null;
  let bestScore = Infinity;
  for (let power = exponent - 2; power <= exponent + 2; power++)
    for (const factor of [1, 2, 5]) {
      const meters = factor * 10 ** power * base,
        pixels = meters / metersPerPixel;
      const score =
        pixels >= minPx && pixels <= maxPx
          ? Math.abs(pixels - (minPx + maxPx) / 2)
          : 1000 + Math.min(Math.abs(pixels - minPx), Math.abs(pixels - maxPx));
      if (score < bestScore) {
        bestScore = score;
        best = { meters, pixels, label: formatDistance(meters, units) };
      }
    }
  return best!;
}

function cellRect(map: GridMap, column: number, row: number) {
  const minX = -map.grid.offsetX + column * 10000;
  const maxZ = map.grid.offsetY - row * 10000;
  return { minX, minZ: maxZ - 10000, maxX: minX + 10000, maxZ };
}

/** Resolve a catalog grid cell or a point in centered world meters. Grid columns are zero-based. */
export function parseLocation(input: string, map: GridMap): ParsedLocation | null {
  const value = input.trim();
  const grid = /^([a-z]+)(\d+)$/i.exec(value);
  if (grid) {
    let row = 0;
    for (const letter of grid[1].toUpperCase()) row = row * 26 + letter.charCodeAt(0) - 64;
    row--;
    const column = Number(grid[2]);
    if (
      !Number.isSafeInteger(row) ||
      row < 0 ||
      row >= map.grid.rows ||
      !Number.isSafeInteger(column) ||
      column >= map.grid.columns
    )
      return null;
    const cell = cellRect(map, column, row);
    return {
      kind: "grid",
      reference: `${gridRowLabel(row)}${column}`,
      x: (cell.minX + cell.maxX) / 2,
      z: (cell.minZ + cell.maxZ) / 2,
      cell,
    };
  }
  const number = "([+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+))";
  const marked = new RegExp(`^x\\s*=?\\s*${number}\\s*,?\\s*z\\s*=?\\s*${number}$`, "i").exec(
    value,
  );
  const plain = new RegExp(`^${number}(?:\\s*,\\s*|\\s+)${number}$`).exec(value);
  const match = marked ?? plain;
  if (!match) return null;
  const x = Number(match[1]),
    z = Number(match[2]);
  if (
    !Number.isFinite(x) ||
    !Number.isFinite(z) ||
    Math.abs(x) > map.widthMeters / 2 ||
    Math.abs(z) > map.heightMeters / 2
  )
    return null;
  return { kind: "point", x, z };
}
