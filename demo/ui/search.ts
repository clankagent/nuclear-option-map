import type { MapDefinition } from "../../src/catalog";
import { parseLocation, type ParsedLocation } from "./geo";
export type SearchResult = {
  label: string;
  detail: string;
  x: number;
  z: number;
  location?: ParsedLocation;
};
export function searchLocations(query: string, map: MapDefinition): SearchResult[] {
  const value = query.trim();
  if (!value) return [];
  const location = parseLocation(value, map);
  const results: SearchResult[] = location
    ? [
        {
          label:
            location.kind === "grid"
              ? `Grid ${location.reference}`
              : `x ${Math.round(location.x)}, z ${Math.round(location.z)}`,
          detail: location.kind === "grid" ? "10 km cell" : "Map coordinates",
          x: location.x,
          z: location.z,
          location,
        },
      ]
    : [];
  return results;
}
