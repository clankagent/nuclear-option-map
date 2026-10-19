import { describe, expect, it } from 'vite-plus/test';
import { DEFAULT_SETTINGS, exportPins, loadPins, loadSettings, mergePins, parsePinImport, savePins, saveSettings, type Pin } from '../../demo/ui/store';

function memory(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
}
const saved = (version: number, value: object) => memory({ [`nuclear-option-map.settings.v${version}`]: JSON.stringify(value) });
const pin: Pin = { id: 'pin-1', name: 'Ramp', x: 120, z: -80, color: 'green' };
const maps = [{ id: 'heartland', widthMeters: 1000, heightMeters: 800 }];

describe('demo settings persistence', () => {
  it('uses v4 defaults and saves new preferences', () => {
    expect(loadSettings(memory())).toEqual(DEFAULT_SETTINGS);
    const storage = memory();
    saveSettings({ ...DEFAULT_SETTINGS, units: 'nautical', minimap: false, minimapCollapsed: true }, storage);
    expect(loadSettings(storage)).toMatchObject({ units: 'nautical', minimap: false, minimapCollapsed: true });
  });
  it('preserves the exact legacy interval and topo migration gates', () => {
    const legacy = { grid: false, labels: 'cells', tint: '#ABCDEF', contourInterval: 100, contourSmoothingMeters: 60, topoStyle: 'lowlands-dark', topoStrength: .7, topoHillshade: false };
    expect(loadSettings(saved(1, legacy))).toMatchObject({ grid: false, labels: 'cells', tint: '#abcdef', contourInterval: 'auto', contourSmoothingMeters: 60, topoStyle: 'elevation-colors', topoStrength: .7, topoHillshade: true });
    expect(loadSettings(saved(2, legacy))).toMatchObject({ contourInterval: 100, topoStyle: 'elevation-colors', topoHillshade: true });
    expect(loadSettings(saved(3, legacy))).toMatchObject({ contourInterval: 100, topoStyle: 'lowlands-dark', topoHillshade: false });
    expect(loadSettings(saved(3, { topoStyle: 'elevation-colours' })).topoStyle).toBe('elevation-colors');
    expect(loadSettings(saved(3, { contourSmoothingMeters: 81, tint: 'red' }))).toMatchObject({ contourSmoothingMeters: 40, tint: '#6f9d75' });
  });
  it('uses defaults when storage or JSON fails', () => {
    expect(loadSettings({ getItem: () => { throw new Error('blocked'); } })).toEqual(DEFAULT_SETTINGS);
    expect(loadSettings(memory({ 'nuclear-option-map.settings.v4': '{' }))).toEqual(DEFAULT_SETTINGS);
    expect(() => saveSettings(DEFAULT_SETTINGS, { setItem: () => { throw new Error('blocked'); } })).not.toThrow();
  });
});

describe('map pins', () => {
  it('saves per-map pins, merges by ID and caps at 200', () => {
    const storage = memory();
    savePins('heartland', [pin], storage);
    savePins('ignus', [{ ...pin, id: 'pin-2' }], storage);
    expect(loadPins('heartland', storage)).toEqual([pin]);
    expect(loadPins('ignus', storage)).toHaveLength(1);
    expect(mergePins([pin], [{ ...pin, name: 'Replacement' }, { ...pin, id: 'pin-2' }])).toEqual([pin, { ...pin, id: 'pin-2' }]);
    expect(mergePins(Array.from({ length: 200 }, (_, i) => ({ ...pin, id: `pin-${i}` })), [{ ...pin, id: 'extra' }])).toHaveLength(200);
    expect(() => savePins('heartland', [pin], { getItem: () => null, setItem: () => { throw new Error('blocked'); } })).not.toThrow();
    expect(loadPins('heartland', { getItem: () => { throw new Error('blocked'); } })).toEqual([]);
  });
  it('exports and validates a bounded schema-one import', () => {
    const exported = exportPins({ heartland: [pin] });
    expect(parsePinImport(exported, maps)).toEqual({ ok: true, maps: { heartland: [pin] } });
    const invalid = (patch: object) => JSON.stringify({ schemaVersion: 1, maps: { heartland: [{ ...pin, ...patch }] } });
    for (const patch of [{ x: Infinity }, { x: 501 }, { name: '' }, { name: 'x'.repeat(41) }, { color: 'purple' }, { id: 'bad_id' }]) {
      // JSON encodes Infinity as null, which must still be rejected.
      expect(parsePinImport(invalid(patch), maps).ok).toBe(false);
    }
    expect(parsePinImport(JSON.stringify({ schemaVersion: 2, maps: {} }), maps).ok).toBe(false);
    expect(parsePinImport(JSON.stringify({ schemaVersion: 1, maps: { unknown: [pin] } }), maps).ok).toBe(false);
    expect(parsePinImport('x'.repeat(256 * 1024 + 1), maps).ok).toBe(false);
    expect(parsePinImport('{', maps).ok).toBe(false);
  });
});
