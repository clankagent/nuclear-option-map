import type { MapDefinition } from "../../src/catalog";
import { parseLocation, type ParsedLocation } from "./geo";
import type { Pin } from "./store";
export type SearchResult = {
  label: string;
  detail: string;
  x: number;
  z: number;
  location?: ParsedLocation;
  pinId?: string;
};
export function searchLocations(query: string, map: MapDefinition, pins: Pin[]): SearchResult[] {
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
  const terms = value.toLowerCase().split(/\s+/);
  results.push(
    ...pins
      .filter((pin) => terms.every((term) => pin.name.toLowerCase().includes(term)))
      .slice(0, 8)
      .map((pin) => ({ label: pin.name, detail: "Saved pin", x: pin.x, z: pin.z, pinId: pin.id })),
  );
  return results;
}
