import { createMapViewer, loadMapCatalog, validateMapCatalog } from '../src/index.ts';
import type { MapDefinition, ContourPreference, ContourInterval } from '../src/index.ts';

type Viewer = Awaited<ReturnType<typeof createMapViewer>>;
type ViewState = Viewer['state'];
type Mode = 'builtin' | 'raw';
type Labels = 'edges' | 'cells' | 'none';
type Variant = { id: string; label: string; image: string; kind: 'builtin' | 'terrain'; tiles?: MapDefinition['tiles']; contours?: MapDefinition['contours'] };
type Settings = { grid: boolean; labels: Labels; tint: string; contourInterval: ContourPreference; contourSmoothingMeters: number };

const ORIGINAL = 'original';
const SETTINGS_KEY = 'nuclear-option-map.settings.v2';
const VARIANTS_URL = '/maps/variants.json';
const DEFAULTS: Settings = { grid: true, labels: 'edges', tint: '#6f9d75', contourInterval: 'auto', contourSmoothingMeters: 40 };

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const canvas = $<HTMLCanvasElement>('map-canvas');
const mapSelect = $<HTMLSelectElement>('map-select');
const variantField = $('variant-field');
const variantSelect = $<HTMLSelectElement>('variant-select');
const modeSelect = $<HTMLSelectElement>('mode-select');
const gridToggle = $<HTMLInputElement>('grid-toggle');
const labelsSelect = $<HTMLSelectElement>('labels-select');
const tintInput = $<HTMLInputElement>('tint-input');
const contourField = $('contour-field');
const contourSelect = $<HTMLSelectElement>('contour-select');
const smoothingField = $('smoothing-field');
const smoothingInput = $<HTMLInputElement>('smoothing-input');
const smoothingValue = $('smoothing-value');
const intervalEl = $('interval');
const zoomInBtn = $<HTMLButtonElement>('zoom-in');
const zoomOutBtn = $<HTMLButtonElement>('zoom-out');
const fitBtn = $<HTMLButtonElement>('fit');
const overlay = $('overlay');
const overlayTitle = $('overlay-title');
const overlayText = $('overlay-text');
const engineEl = $('engine');
const coordsEl = $('coords');
const zoomEl = $('zoom');
const sizeEl = $('size');
const popovers = Array.from(document.querySelectorAll<HTMLDetailsElement>('details.pop'));

let viewer: Viewer | null = null;
let maps: MapDefinition[] = [];
let variants = new Map<string, Variant[]>();
let current: MapDefinition | null = null;
let currentVariant = ORIGINAL;
let userMode: Mode = 'builtin';
let loading = false;
let mapLoaded = false;
let switchToken = 0;

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const num = (v: number, digits = 1) => v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
const isLabels = (v: unknown): v is Labels => v === 'edges' || v === 'cells' || v === 'none';
const isTint = (v: unknown): v is string => typeof v === 'string' && /^#[\da-f]{6}$/i.test(v);

function loadSettings(): Settings {
  const out = { ...DEFAULTS };
  try {
    const currentSettings = localStorage.getItem(SETTINGS_KEY);
    const raw = JSON.parse(currentSettings ?? localStorage.getItem('nuclear-option-map.settings.v1') ?? 'null') as Partial<Record<keyof Settings, unknown>> | null;
    if (raw && typeof raw === 'object') {
      if (typeof raw.grid === 'boolean') out.grid = raw.grid;
      if (isLabels(raw.labels)) out.labels = raw.labels;
      if (isTint(raw.tint)) out.tint = raw.tint.toLowerCase();
      if (raw.contourInterval === 'auto' || raw.contourInterval === 10 || raw.contourInterval === 20 || raw.contourInterval === 50 || (currentSettings && raw.contourInterval === 100)) out.contourInterval = raw.contourInterval;
      if (typeof raw.contourSmoothingMeters === 'number' && Number.isFinite(raw.contourSmoothingMeters) && raw.contourSmoothingMeters >= 0 && raw.contourSmoothingMeters <= 80) out.contourSmoothingMeters = raw.contourSmoothingMeters;
    }
  } catch { /* blocked storage or invalid data: use defaults */ }
  return out;
}

function saveSettings() {
  try {
    const s: Settings = { grid: gridToggle.checked, labels: labelsSelect.value as Labels, tint: tintInput.value, contourInterval: contourPreference, contourSmoothingMeters: Number(smoothingInput.value) };
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
  } catch { /* storage unavailable; settings just won't persist */ }
}

function showOverlay(title: string, text = '', error = false) {
  overlayTitle.textContent = title;
  overlayText.textContent = text;
  overlay.classList.toggle('error', error);
  overlay.hidden = false;
}
const hideOverlay = () => { overlay.hidden = true; };
let contourPreference: ContourPreference = 'auto';

const variantKind = () => variants.get(current?.id ?? '')?.find(v => v.id === currentVariant)?.kind ?? 'builtin';
const terrainActive = () => variantKind() === 'terrain';
const effectiveMode = (): Mode => (terrainActive() ? 'raw' : userMode);

function syncControls() {
  const ready = viewer !== null && !loading;
  const hasMap = ready && mapLoaded;
  mapSelect.disabled = !ready || maps.length === 0;
  variantSelect.disabled = !hasMap;
  modeSelect.disabled = !hasMap || terrainActive();
  $('mode-field').hidden = terrainActive();
  modeSelect.value = effectiveMode();
  gridToggle.disabled = !hasMap;
  labelsSelect.disabled = !hasMap;
  tintInput.disabled = !hasMap || effectiveMode() !== 'builtin';
  const contourIntervals = viewer?.state.map?.contours?.intervals ?? [];
  contourField.hidden = contourIntervals.length === 0;
  smoothingField.hidden = contourIntervals.length === 0;
  contourSelect.disabled = !hasMap;
  smoothingInput.disabled = !hasMap;
  for (const option of contourSelect.options) option.disabled = option.value !== 'auto' && !contourIntervals.includes(Number(option.value) as ContourInterval);
  contourSelect.value = String(contourPreference);
  zoomInBtn.disabled = zoomOutBtn.disabled = fitBtn.disabled = !hasMap;
}

function fillVariants(mapId: string) {
  const list = variants.get(mapId) ?? [];
  variantField.hidden = list.length === 0;
  if (list.length === 0) { variantSelect.replaceChildren(); return; }
  variantSelect.replaceChildren(new Option('Builtin', ORIGINAL), ...list.map(v => new Option(v.label, v.id)));
  variantSelect.value = currentVariant;
}

function showSize(map: MapDefinition | null) {
  sizeEl.textContent = map ? `Map ${num(map.widthMeters, 0)} × ${num(map.heightMeters, 0)} m` : 'Map —';
}

function onViewChange(state: ViewState) {
  zoomEl.textContent = `Zoom ${num(state.zoom, 2)}×`;
  intervalEl.textContent = state.contourInterval ? `Contours ${state.contourInterval} m${state.contourPreference === 'auto' ? ' · auto' : ''}` : '';
  intervalEl.hidden = !state.contourInterval;
  if (state.map) showSize(state.map);
}

function onPointerMove(p: { x: number; z: number; insideMap: boolean; gridReference?: string | null } | null) {
  coordsEl.textContent = p
    ? `Grid ${p.gridReference ?? '—'} · x ${num(p.x)} m · z ${num(p.z)} m${p.insideMap ? '' : ' (outside map)'}`
    : 'Grid — · x — · z —';
}

/** Load a map's original image or one of its variants; geometry always comes from the catalog entry. */
async function showMap(map: MapDefinition, variantId: string) {
  if (!viewer) return;
  const variant = variants.get(map.id)?.find(v => v.id === variantId);
  const sameMap = current?.id === map.id;
  const token = ++switchToken;
  loading = true;
  if (!sameMap) mapLoaded = false;
  syncControls();
  showOverlay('Loading map', variant ? `${map.name} · ${variant.label}` : map.name);
  try {
    viewer.setMode(variant?.kind === 'terrain' ? 'raw' : userMode);
    await viewer.setMap(variant ? { ...map, image: variant.image, tiles: variant.tiles, contours: variant.contours } : map, { preserveView: sameMap });
    if (token !== switchToken) return;
    current = map;
    currentVariant = variant ? variant.id : ORIGINAL;
    mapLoaded = true;
    viewer.setMode(effectiveMode());
    fillVariants(map.id);
    hideOverlay();
    onPointerMove(null);
    showSize(map);
  } catch (e) {
    if (token !== switchToken) return;
    viewer.setMode(effectiveMode());
    if (sameMap && mapLoaded) {
      // The viewer keeps the previous image on failure, so stay usable.
      variantSelect.value = currentVariant;
      showOverlay(`Could not load ${variant?.label ?? 'view'}`, message(e), true);
    } else {
      showOverlay(`Could not load ${map.name}`, message(e), true);
      showSize(null);
    }
  } finally {
    if (token === switchToken) {
      loading = false;
      syncControls();
    }
  }
}

function selectMap(id: string) {
  const map = maps.find(m => m.id === id);
  if (!map) return Promise.resolve();
  return showMap(map, ORIGINAL);
}

function bindControls() {
  mapSelect.addEventListener('change', () => { void selectMap(mapSelect.value); });
  variantSelect.addEventListener('change', () => { if (current) void showMap(current, variantSelect.value); });
  modeSelect.addEventListener('change', () => {
    userMode = modeSelect.value as Mode;
    viewer?.setMode(effectiveMode());
    syncControls();
  });
  gridToggle.addEventListener('change', () => { viewer?.setGrid(gridToggle.checked); saveSettings(); });
  labelsSelect.addEventListener('change', () => { viewer?.setGridLabels(labelsSelect.value as Labels); saveSettings(); });
  tintInput.addEventListener('input', () => { viewer?.setTint(tintInput.value); saveSettings(); });
  contourSelect.addEventListener('change', async () => {
    if (!viewer || loading) return;
    contourSelect.disabled = true;
    try {
      const interval: ContourPreference = contourSelect.value === 'auto' ? 'auto' : Number(contourSelect.value) as ContourInterval;
      await viewer.setContourInterval(interval);
      contourPreference = interval;
      saveSettings();
      hideOverlay();
    }
    catch (e) { showOverlay('Could not load contours', message(e), true); }
    finally { syncControls(); }
  });
  smoothingInput.addEventListener('input', () => {
    const meters = Number(smoothingInput.value);
    viewer?.setContourSmoothing(meters);
    smoothingValue.textContent = `${meters} m`;
    saveSettings();
  });
  zoomInBtn.addEventListener('click', () => viewer?.zoomIn());
  zoomOutBtn.addEventListener('click', () => viewer?.zoomOut());
  fitBtn.addEventListener('click', () => viewer?.fit());
  canvas.addEventListener('pointerleave', () => onPointerMove(null));

  // Popovers: one open at a time; close on outside click or Escape.
  for (const d of popovers) {
    d.addEventListener('toggle', () => {
      if (d.open) for (const o of popovers) if (o !== d) o.open = false;
    });
  }
  document.addEventListener('pointerdown', e => {
    for (const d of popovers) if (d.open && !d.contains(e.target as Node)) d.open = false;
  });
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    for (const d of popovers) if (d.open) { d.open = false; d.querySelector('summary')?.focus(); }
  });
}

function fillMapList() {
  mapSelect.replaceChildren(...maps.map(m => new Option(m.name, m.id)));
}

/** Optional private variant list. Absent or malformed means no extra views; nothing is invented. */
async function loadVariants() {
  try {
    const response = await fetch(VARIANTS_URL);
    if (!response.ok) return;
    const data = await response.json() as { schemaVersion?: unknown; maps?: Record<string, unknown> };
    if (data?.schemaVersion !== 1 || !data.maps || typeof data.maps !== 'object') return;
    const base = response.url || new URL(VARIANTS_URL, location.href).href;
    for (const [mapId, list] of Object.entries(data.maps)) {
      if (!Array.isArray(list) || !maps.some(m => m.id === mapId)) continue;
      const ids = new Set<string>([ORIGINAL]);
      const ok: Variant[] = [];
      for (const v of list as Record<string, unknown>[]) {
        if (!v || typeof v.id !== 'string' || !v.id || ids.has(v.id)) continue;
        if (typeof v.label !== 'string' || !v.label.trim() || typeof v.image !== 'string' || !v.image.trim()) continue;
        if (v.kind !== 'builtin' && v.kind !== 'terrain') continue;
        if (!(Number(v.widthPixels) > 0) || !(Number(v.heightPixels) > 0)) continue;
        let definition: MapDefinition;
        try {
          const original = maps.find(m => m.id === mapId)!;
          definition = validateMapCatalog({ schemaVersion: 1, maps: [{ ...original, image: v.image, tiles: v.tiles, contours: v.contours }] }, base).maps[0];
          if (!['http:', 'https:'].includes(new URL(definition.image).protocol)) continue;
        } catch { continue; }
        ids.add(v.id);
        ok.push({ id: v.id, label: v.label.trim(), image: definition.image, tiles: definition.tiles, contours: definition.contours, kind: v.kind });
      }
      if (ok.length) variants.set(mapId, ok);
    }
  } catch { /* optional file */ }
}

async function init() {
  const saved = loadSettings();
  contourPreference = saved.contourInterval;
  smoothingInput.value = String(saved.contourSmoothingMeters);
  smoothingValue.textContent = `${saved.contourSmoothingMeters} m`;
  gridToggle.checked = saved.grid;
  labelsSelect.value = saved.labels;
  tintInput.value = saved.tint;
  bindControls();
  showOverlay('Starting', 'Initializing map engine…');
  try {
    viewer = await createMapViewer(canvas, {
      mode: modeSelect.value as Mode,
      grid: saved.grid,
      gridLabels: saved.labels,
      tint: saved.tint,
      contourInterval: saved.contourInterval,
      contourSmoothingMeters: saved.contourSmoothingMeters,
      onViewChange,
      onPointerMove,
    });
    engineEl.textContent = 'Rust / WASM';
  } catch (e) {
    engineEl.textContent = 'Engine unavailable';
    showOverlay('Map engine failed to start', message(e), true);
    syncControls();
    return;
  }

  showOverlay('Loading map catalog', '/maps/manifest.json');
  try {
    maps = (await loadMapCatalog('/maps/manifest.json')).maps;
  } catch (e) {
    const missing = /\(404\)/.test(message(e));
    showOverlay(
      missing ? 'No local map installed' : 'Could not read map catalog',
      missing ? 'Expected demo/public/maps/manifest.json. Run the extractor to create it.' : message(e),
      !missing,
    );
    syncControls();
    return;
  }

  if (maps.length === 0) {
    showOverlay('No local map installed', 'The catalog in demo/public/maps/manifest.json lists no maps.');
    syncControls();
    return;
  }

  await loadVariants();
  fillMapList();
  syncControls();
  await selectMap(maps[0].id);
}

void init();
