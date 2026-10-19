import { validateMapCatalog, type MapDefinition } from "../src/index.ts";
export type Variant = {
  id: string;
  label: string;
  image: string;
  kind: "builtin" | "terrain";
  tiles?: MapDefinition["tiles"];
  contours?: MapDefinition["contours"];
  topography?: MapDefinition["topography"];
};
export const ORIGINAL = "original";
const VARIANTS_URL = "/maps/variants.json";
/** Optional private variant list. Absent or malformed means no extra views. */
export async function loadVariants(
  maps: MapDefinition[],
  signal?: AbortSignal,
): Promise<Map<string, Variant[]> | null> {
  try {
    const response = await fetch(VARIANTS_URL, { cache: "no-store", signal });
    if (!response.ok) return null;
    const data = JSON.parse(await response.text()) as {
      schemaVersion?: unknown;
      maps?: Record<string, unknown>;
    };
    if (data?.schemaVersion !== 1 || !data.maps || typeof data.maps !== "object") return null;
    const base = response.url || new URL(VARIANTS_URL, location.href).href;
    const next = new Map<string, Variant[]>();
    for (const [mapId, list] of Object.entries(data.maps)) {
      if (!Array.isArray(list) || !maps.some((m) => m.id === mapId)) continue;
      const ids = new Set<string>([ORIGINAL]),
        valid: Variant[] = [];
      for (const v of list as Record<string, unknown>[]) {
        if (
          !v ||
          typeof v.id !== "string" ||
          !v.id ||
          ids.has(v.id) ||
          !["satellite", "topographic"].includes(v.id)
        )
          continue;
        if (
          typeof v.label !== "string" ||
          !v.label.trim() ||
          typeof v.image !== "string" ||
          !v.image.trim()
        )
          continue;
        if (v.kind !== "builtin" && v.kind !== "terrain") continue;
        if (!(Number(v.widthPixels) > 0) || !(Number(v.heightPixels) > 0)) continue;
        try {
          const original = maps.find((m) => m.id === mapId)!;
          const definition = validateMapCatalog(
            {
              schemaVersion: 1,
              maps: [
                {
                  ...original,
                  image: v.image,
                  tiles: v.tiles,
                  contours: v.contours,
                  topography: v.topography,
                },
              ],
            },
            base,
          ).maps[0];
          if (!["http:", "https:"].includes(new URL(definition.image).protocol)) continue;
          ids.add(v.id);
          valid.push({
            id: v.id,
            label: v.label.trim(),
            image: definition.image,
            tiles: definition.tiles,
            contours: definition.contours,
            topography: definition.topography,
            kind: v.kind,
          });
        } catch {
          /* Ignore malformed optional views. */
        }
      }
      if (valid.length) next.set(mapId, valid);
    }
    signal?.throwIfAborted();
    return next;
  } catch (error) {
    if (signal?.aborted) throw error;
  }
  return null;
}
