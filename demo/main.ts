import { createMapViewer, loadMapCatalog } from '../src/index.ts';
import type { MapDefinition } from '../src/index.ts';

type Viewer = Awaited<ReturnType<typeof createMapViewer>>;
type ViewState = Viewer['state'];
type Mode = 'builtin' | 'raw';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const canvas = $<HTMLCanvasElement>('map-canvas');
const mapSelect = $<HTMLSelectElement>('map-select');
const modeSelect = $<HTMLSelectElement>('mode-select');
const gridToggle = $<HTMLInputElement>('grid-toggle');
const tintInput = $<HTMLInputElement>('tint-input');
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

let viewer: Viewer | null = null;
let maps: MapDefinition[] = [];
let loading = false;
let mapLoaded = false;
let switchToken = 0;

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const num = (v: number, digits = 1) => v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });

function showOverlay(title: string, text = '', error = false) {
  overlayTitle.textContent = title;
  overlayText.textContent = text;
  overlay.classList.toggle('error', error);
  overlay.hidden = false;
}
const hideOverlay = () => { overlay.hidden = true; };

function syncControls() {
  const ready = viewer !== null && !loading;
  const hasMap = ready && mapLoaded;
  mapSelect.disabled = !ready || maps.length === 0;
  modeSelect.disabled = !hasMap;
  gridToggle.disabled = !hasMap;
  tintInput.disabled = !hasMap || modeSelect.value !== 'builtin';
  zoomInBtn.disabled = zoomOutBtn.disabled = fitBtn.disabled = !hasMap;
}

function showSize(map: MapDefinition | null) {
  sizeEl.textContent = map ? `Map ${num(map.widthMeters, 0)} × ${num(map.heightMeters, 0)} m` : 'Map —';
}

function onViewChange(state: ViewState) {
  zoomEl.textContent = `Zoom ${num(state.zoom, 2)}×`;
  if (state.map) showSize(state.map);
}

function onPointerMove(p: { x: number; z: number; insideMap: boolean } | null) {
  coordsEl.textContent = p
    ? `x ${num(p.x)} m · z ${num(p.z)} m${p.insideMap ? '' : ' (outside map)'}`
    : 'x — · z —';
}

async function selectMap(id: string) {
  const map = maps.find(m => m.id === id);
  if (!viewer || !map) return;
  const token = ++switchToken;
  loading = true;
  mapLoaded = false;
  syncControls();
  showOverlay('Loading map', map.name);
  try {
    await viewer.setMap(map);
    if (token !== switchToken) return;
    mapLoaded = true;
    hideOverlay();
    showSize(map);
  } catch (e) {
    if (token !== switchToken) return;
    showOverlay(`Could not load ${map.name}`, message(e), true);
    showSize(null);
  } finally {
    if (token === switchToken) {
      loading = false;
      syncControls();
    }
  }
}

function bindControls() {
  mapSelect.addEventListener('change', () => { void selectMap(mapSelect.value); });
  modeSelect.addEventListener('change', () => {
    viewer?.setMode(modeSelect.value as Mode);
    syncControls();
  });
  gridToggle.addEventListener('change', () => viewer?.setGrid(gridToggle.checked));
  tintInput.addEventListener('input', () => viewer?.setTint(tintInput.value));
  zoomInBtn.addEventListener('click', () => viewer?.zoomIn());
  zoomOutBtn.addEventListener('click', () => viewer?.zoomOut());
  fitBtn.addEventListener('click', () => viewer?.fit());
  canvas.addEventListener('pointerleave', () => onPointerMove(null));
}

function fillMapList() {
  mapSelect.replaceChildren(...maps.map(m => new Option(m.name, m.id)));
}

async function init() {
  bindControls();
  showOverlay('Starting', 'Initializing map engine…');
  try {
    viewer = await createMapViewer(canvas, {
      mode: modeSelect.value as Mode,
      grid: gridToggle.checked,
      tint: tintInput.value,
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

  fillMapList();
  syncControls();
  await selectMap(maps[0].id);
}

void init();
