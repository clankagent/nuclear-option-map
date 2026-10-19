export type SharedPin = { name: string; x: number; z: number };
export type ViewHash = {
  map?: string;
  variant?: string;
  centerX?: number;
  centerZ?: number;
  zoom?: number;
  sharedPins?: SharedPin[];
};

const idPattern = /^[a-z0-9][a-z0-9_-]{0,63}$/i;
const controls = /[\u0000-\u001f\u007f-\u009f]/g;
const coordinate = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= 1e6;
const zoom = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0.25 && value <= 1024;
const cleanName = (value: unknown): string | null => {
  if (typeof value !== "string") return null;
  const name = value.replace(controls, "").trim();
  return name.length >= 1 && name.length <= 40 ? name : null;
};
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
    const shared = fields.get("p");
    if (shared) {
      const pins: SharedPin[] = [];
      for (const entry of shared.split(",")) {
        if (pins.length === 8) break;
        const pieces = entry.split("~");
        if (pieces.length !== 3) continue;
        const name = cleanName(decode(pieces[0])),
          x = numberValue(pieces[1]),
          z = numberValue(pieces[2]);
        if (name && coordinate(x) && coordinate(z)) pins.push({ name, x, z });
      }
      if (pins.length) result.sharedPins = pins;
    }
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
  const pins =
    view.sharedPins?.slice(0, 8).flatMap((pin) => {
      const name = cleanName(pin.name);
      return name && coordinate(pin.x) && coordinate(pin.z)
        ? [
            `${encodeURIComponent(name).replace(/~/g, "%7E")}~${Math.round(pin.x)}~${Math.round(pin.z)}`,
          ]
        : [];
    }) ?? [];
  if (pins.length) fields.push(`p=${pins.join(",")}`);
  return `#${fields.join("&")}`;
}
