import type { ContourPreference, GridLabels } from "../../src/viewer";
import type { TopoStyle } from "../../src/topo-style";
import type { UnitSystem } from "./geo";

export type Settings = {
  grid: boolean;
  labels: GridLabels;
  tint: string;
  contourInterval: ContourPreference;
  contourSmoothingMeters: number;
  topoStyle: TopoStyle;
  topoStrength: number;
  topoHillshade: boolean;
  units: UnitSystem;
  speedKnots: number;
  minimap: boolean;
  minimapCollapsed: boolean;
};
export const DEFAULT_SETTINGS: Settings = {
  grid: true,
  labels: "edges",
  tint: "#6f9d75",
  contourInterval: "auto",
  contourSmoothingMeters: 40,
  topoStyle: "elevation-colors",
  topoStrength: 0.45,
  topoHillshade: true,
  units: "metric",
  speedKnots: 600,
  minimap: true,
  minimapCollapsed: false,
};
const SETTINGS_KEY = "nuclear-option-map.settings.v4";
const PINS_KEY = "nuclear-option-map.pins.v1";
const isLabels = (v: unknown): v is GridLabels => v === "edges" || v === "cells" || v === "none";
const isTint = (v: unknown): v is string => typeof v === "string" && /^#[\da-f]{6}$/i.test(v);

export function loadSettings(storage?: Pick<Storage, "getItem">): Settings {
  const out = { ...DEFAULT_SETTINGS };
  try {
    const source = storage ?? localStorage;
    const currentSettings = source.getItem(SETTINGS_KEY);
    const v3 = source.getItem("nuclear-option-map.settings.v3");
    const previousSettings = source.getItem("nuclear-option-map.settings.v2");
    const raw = JSON.parse(
      currentSettings ??
        v3 ??
        previousSettings ??
        source.getItem("nuclear-option-map.settings.v1") ??
        "null",
    ) as Partial<Record<keyof Settings, unknown>> | null;
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      if (typeof raw.grid === "boolean") out.grid = raw.grid;
      if (isLabels(raw.labels)) out.labels = raw.labels;
      if (isTint(raw.tint)) out.tint = raw.tint.toLowerCase();
      if (
        raw.contourInterval === "auto" ||
        raw.contourInterval === 10 ||
        raw.contourInterval === 20 ||
        raw.contourInterval === 50 ||
        ((currentSettings || v3 || previousSettings) && raw.contourInterval === 100)
      )
        out.contourInterval = raw.contourInterval;
      if (
        typeof raw.contourSmoothingMeters === "number" &&
        Number.isFinite(raw.contourSmoothingMeters) &&
        raw.contourSmoothingMeters >= 0 &&
        raw.contourSmoothingMeters <= 80
      )
        out.contourSmoothingMeters = raw.contourSmoothingMeters;
      if (raw.topoStyle === "elevation-colours") raw.topoStyle = "elevation-colors";
      // Existing v2 preferences adopt the new requested default; v3 keeps subsequent choices.
      if (
        (currentSettings || v3) &&
        (raw.topoStyle === "plain" ||
          raw.topoStyle === "lowlands-dark" ||
          raw.topoStyle === "elevation-colors")
      )
        out.topoStyle = raw.topoStyle;
      if (
        typeof raw.topoStrength === "number" &&
        Number.isFinite(raw.topoStrength) &&
        raw.topoStrength >= 0 &&
        raw.topoStrength <= 1
      )
        out.topoStrength = raw.topoStrength;
      if ((currentSettings || v3) && typeof raw.topoHillshade === "boolean")
        out.topoHillshade = raw.topoHillshade;
      if (currentSettings) {
        if (raw.units === "metric" || raw.units === "nautical") out.units = raw.units;
        if (typeof raw.speedKnots === "number" && Number.isFinite(raw.speedKnots))
          out.speedKnots = Math.max(50, Math.min(2000, raw.speedKnots));
        if (typeof raw.minimap === "boolean") out.minimap = raw.minimap;
        if (typeof raw.minimapCollapsed === "boolean") out.minimapCollapsed = raw.minimapCollapsed;
      }
    }
  } catch {
    /* Storage can be blocked or contain invalid JSON. */
  }
  return out;
}

export function saveSettings(settings: Settings, storage?: Pick<Storage, "setItem">): void {
  try {
    (storage ?? localStorage).setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    /* Keep in-memory settings usable. */
  }
}

export const PIN_COLORS = ["green", "amber", "red", "blue", "white"] as const;
export type PinColor = (typeof PIN_COLORS)[number];
export type Pin = { id: string; name: string; x: number; z: number; color: PinColor };
export type PinMaps = Record<string, Pin[]>;
const pinId = /^[a-z0-9-]{1,40}$/i;
const validPin = (value: unknown): value is Pin => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const pin = value as Partial<Pin>;
  return (
    typeof pin.id === "string" &&
    pinId.test(pin.id) &&
    typeof pin.name === "string" &&
    pin.name.length >= 1 &&
    pin.name.length <= 40 &&
    typeof pin.x === "number" &&
    Number.isFinite(pin.x) &&
    typeof pin.z === "number" &&
    Number.isFinite(pin.z) &&
    PIN_COLORS.includes(pin.color as PinColor)
  );
};
const objectRecord = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);

export function loadPins(mapId: string, storage?: Pick<Storage, "getItem">): Pin[] {
  try {
    const parsed: unknown = JSON.parse((storage ?? localStorage).getItem(PINS_KEY) ?? "null");
    if (!objectRecord(parsed) || !Array.isArray(parsed[mapId])) return [];
    return parsed[mapId].filter(validPin).slice(0, 200);
  } catch {
    return [];
  }
}
export function savePins(
  mapId: string,
  pins: Pin[],
  storage?: Pick<Storage, "getItem" | "setItem">,
): void {
  try {
    const target = storage ?? localStorage;
    const prior: unknown = JSON.parse(target.getItem(PINS_KEY) ?? "null");
    const maps: PinMaps = objectRecord(prior) ? (prior as PinMaps) : {};
    maps[mapId] = pins.filter(validPin).slice(0, 200);
    target.setItem(PINS_KEY, JSON.stringify(maps));
  } catch {
    /* Storage failures must not block map interaction. */
  }
}
export function exportPins(allPins: PinMaps): string {
  return JSON.stringify({ schemaVersion: 1, maps: allPins }, null, 2);
}

export function parsePinImport(
  text: string,
  maps: { id: string; widthMeters: number; heightMeters: number }[],
): { ok: true; maps: PinMaps } | { ok: false; error: string } {
  const fail = (error: string) => ({ ok: false as const, error });
  if (new TextEncoder().encode(text).length > 256 * 1024) return fail("Pin file exceeds 256 KiB");
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return fail("Pin file is not valid JSON");
  }
  if (!objectRecord(parsed) || parsed.schemaVersion !== 1 || !objectRecord(parsed.maps))
    return fail("Pin file needs schemaVersion 1 and a maps object");
  const known = new Map(maps.map((map) => [map.id, map]));
  const result: PinMaps = Object.create(null) as PinMaps;
  for (const [mapId, value] of Object.entries(parsed.maps)) {
    const map = known.get(mapId);
    if (!map) return fail(`Unknown map: ${mapId}`);
    if (!Array.isArray(value) || value.length > 200)
      return fail(`Map ${mapId} must contain at most 200 pins`);
    const ids = new Set<string>();
    const pins: Pin[] = [];
    for (const pin of value) {
      if (
        !validPin(pin) ||
        Math.abs(pin.x) > map.widthMeters / 2 ||
        Math.abs(pin.z) > map.heightMeters / 2 ||
        ids.has(pin.id)
      )
        return fail(`Invalid pin in map ${mapId}`);
      ids.add(pin.id);
      pins.push({ id: pin.id, name: pin.name, x: pin.x, z: pin.z, color: pin.color });
    }
    result[mapId] = pins;
  }
  return { ok: true, maps: result };
}
export function mergePins(existing: Pin[], incoming: Pin[]): Pin[] {
  const result = existing.slice(0, 200),
    ids = new Set(result.map((pin) => pin.id));
  for (const pin of incoming)
    if (result.length < 200 && !ids.has(pin.id)) {
      result.push(pin);
      ids.add(pin.id);
    }
  return result;
}
export function newPinId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function")
      return crypto.randomUUID();
  } catch {
    /* Use fallback. */
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
}
