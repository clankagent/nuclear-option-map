export type ViewHash = {
  map?: string;
  variant?: string;
  centerX?: number;
  centerZ?: number;
  zoom?: number;
};

const idPattern = /^[a-z0-9][a-z0-9_-]{0,63}$/i;
const coordinate = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= 1e6;
const zoom = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0.25 && value <= 1024;
const decode = (value: string): string | null => {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
};
const numberValue = (value: string): number | null =>
  value.trim() !== "" && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value) ? Number(value) : null;

/** Read untrusted URL fragments without throwing or retaining invalid fields. */
export function parseHash(hash: string): ViewHash {
  const result: ViewHash = {};
  try {
    if (typeof hash !== "string") return result;
    const fields = new Map<string, string>();
    for (const field of hash.replace(/^#/, "").slice(0, 65536).split("&")) {
      const separator = field.indexOf("=");
      if (separator < 0) continue;
      const key = field.slice(0, separator),
        value = field.slice(separator + 1);
      if (value.length <= 2048) fields.set(key, value);
    }
    const map = decode(fields.get("m") ?? ""),
      variant = decode(fields.get("v") ?? "");
    if (map && idPattern.test(map)) result.map = map;
    if (variant && idPattern.test(variant)) result.variant = variant;
    for (const [key, output] of [
      ["cx", "centerX"],
      ["cz", "centerZ"],
    ] as const) {
      const value = numberValue(fields.get(key) ?? "");
      if (coordinate(value)) result[output] = value;
    }
    const parsedZoom = numberValue(fields.get("k") ?? "");
    if (zoom(parsedZoom)) result.zoom = parsedZoom;
  } catch {
    /* malformed hashes have no navigation effect */
  }
  return result;
}

/** Build a compact, deterministic fragment from valid view fields. */
export function buildHash(view: ViewHash): string {
  const fields: string[] = [];
  if (typeof view.map === "string" && idPattern.test(view.map))
    fields.push(`m=${encodeURIComponent(view.map)}`);
  if (typeof view.variant === "string" && idPattern.test(view.variant))
    fields.push(`v=${encodeURIComponent(view.variant)}`);
  if (coordinate(view.centerX)) fields.push(`cx=${Math.round(view.centerX)}`);
  if (coordinate(view.centerZ)) fields.push(`cz=${Math.round(view.centerZ)}`);
  if (zoom(view.zoom)) fields.push(`k=${Number(view.zoom.toFixed(2))}`);
  return `#${fields.join("&")}`;
}
