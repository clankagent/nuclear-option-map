import type { TilePyramid } from './catalog';
import { PyramidRenderer, type RasterView, type TileImage, type TileLoader } from './pyramid';

/** RG stores an unsigned big-endian height code; B stores NW illumination; A masks water/unknown. */
export interface TopographyPyramid { tiles: TilePyramid; minHeightMeters: number; heightStepMeters: number; maxHeightMeters: number }
export type TopoStyle = 'plain' | 'lowlands-dark' | 'elevation-colors' | 'hillshade';
export interface TopoStyleOptions { style: TopoStyle; strength: number; hillshade?: boolean }
const clamp = (n: number, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, n));
export function decodeHeight(red: number, green: number, descriptor: TopographyPyramid) {
  return descriptor.minHeightMeters + (red * 256 + green) * descriptor.heightStepMeters;
}
/** A single 64K palette serves every cached tile. Strength changes never trigger another request. */
export function topographyPalette(descriptor: TopographyPyramid, options: TopoStyleOptions): Uint8ClampedArray<ArrayBuffer> {
  const palette = new Uint8ClampedArray(65536 * 4), strength = clamp(options.strength);
  const stops = [[173, 193, 163], [229, 219, 179], [249, 246, 231]];
  for (let code = 0; code < 65536; code++) {
    const t = clamp((descriptor.minHeightMeters + code * descriptor.heightStepMeters) / Math.max(1, descriptor.maxHeightMeters));
    let red = 246, green = 244, blue = 233;
    if (options.style === 'elevation-colors') {
      const part = t < .55 ? 0 : 1;
      const f = part === 0 ? t / .55 : (t - .55) / .45;
      red = stops[part][0] + (stops[part + 1][0] - stops[part][0]) * f;
      green = stops[part][1] + (stops[part + 1][1] - stops[part][1]) * f;
      blue = stops[part][2] + (stops[part + 1][2] - stops[part][2]) * f;
    } else if (options.style === 'lowlands-dark') { red = 198 + 48 * t; green = 205 + 39 * t; blue = 186 + 47 * t; }
    const i = code * 4; palette[i] = red; palette[i + 1] = green; palette[i + 2] = blue;
    palette[i + 3] = options.style === 'plain' && !options.hillshade ? 0 : Math.round(255 * strength);
  }
  return palette;
}
export function colorizeTopography(packed: Uint8ClampedArray, palette: Uint8ClampedArray, relief: boolean): Uint8ClampedArray<ArrayBuffer> {
  const out = new Uint8ClampedArray(packed.length);
  for (let i = 0; i < packed.length; i += 4) {
    if (!packed[i + 3]) continue;
    const p = (packed[i] * 256 + packed[i + 1]) * 4;
    // Restrained relief: a flat 45-degree-lit plane (180/255) remains neutral.
    const shade = relief ? 1 + (packed[i + 2] - 180) / 255 * .55 : 1;
    out[i] = palette[p] * shade; out[i + 1] = palette[p + 1] * shade; out[i + 2] = palette[p + 2] * shade;
    out[i + 3] = palette[p + 3] * packed[i + 3] / 255;
  }
  return out;
}
/** Elevation fills need CSS density at most; reserve cache space for every visible ancestor. */
export function topographySamplingView(view: RasterView, pyramid: TilePyramid, tileBudget: number): RasterView {
  const density = Math.max(view.width, view.height), requested = density * Math.min(1, view.dpr);
  let level = pyramid.levels.findIndex(l => Math.max(l.width, l.height) >= requested);
  if (level < 0) level = pyramid.levels.length - 1;
  const physicalSide = pyramid.tileSize + (pyramid.gutter ?? 0) * 2;
  for (; level > 0; level--) {
    const l = pyramid.levels[level], size = pyramid.tileSize, wanted = new Set<string>();
    const x0 = Math.max(0, Math.floor(-view.x / view.width * l.width / size));
    const x1 = Math.min(l.columns - 1, Math.floor((view.screenWidth - view.x) / view.width * l.width / size));
    const y0 = Math.max(0, Math.floor(-view.y / view.height * l.height / size));
    const y1 = Math.min(l.rows - 1, Math.floor((view.screenHeight - view.y) / view.height * l.height / size));
    // An arithmetic upper bound avoids iterating enormous synthetic viewports.
    if (Math.max(0, x1 - x0 + 1) * Math.max(0, y1 - y0 + 1) * physicalSide ** 2 * 4 > tileBudget * .8) continue;
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
      for (let z = level, ax = x, ay = y; z >= 0; z--, ax = Math.floor(ax / 2), ay = Math.floor(ay / 2)) wanted.add(`${z}/${ax}/${ay}`);
    }
    if (wanted.size * physicalSide ** 2 * 4 <= tileBudget * .8) break;
  }
  // A density below the chosen level also defeats stale high-level hysteresis.
  const cap = Math.max(pyramid.levels[level].width, pyramid.levels[level].height) * .9;
  return { ...view, dpr: Math.min(1, view.dpr, cap / density) };
}
export class TopoStyleRenderer {
  private renderer: PyramidRenderer;
  private options: TopoStyleOptions = { style: 'plain', strength: .45 };
  private palette: Uint8ClampedArray;
  private revision = 0;
  private remaining = 2;
  private needsPaint = false;
  private disposed = false;
  private layer: HTMLCanvasElement | undefined;
  private maxLayerEdge: number;
  private tileBudget: number;
  constructor(private descriptor: TopographyPyramid, private redraw: () => void, budget = 24 * 1024 * 1024, sourceLoader?: TileLoader) {
    const root = descriptor.tiles.levels[0], gutter = descriptor.tiles.gutter ?? 0;
    if (!root || root.columns * root.rows > 4) throw new TypeError('Topography root must contain at most four tiles');
    const rootBytes = (root.width + root.columns * gutter * 2) * (root.height + root.rows * gutter * 2) * 4;
    if (!Number.isFinite(budget) || budget < rootBytes * 2 + 4) throw new TypeError('Topography budget must fit packed root tiles, display canvases and scratch');
    const layerBudget = Math.min(16 * 1024 * 1024, budget * 2 / 3, budget - rootBytes * 2);
    this.maxLayerEdge = Math.max(1, Math.floor(Math.sqrt(layerBudget / 4)));
    this.tileBudget = (budget - layerBudget) / 2;
    this.palette = topographyPalette(descriptor, { ...this.options, strength: 1 });
    const loader: TileLoader = async (url, signal) => {
      const source = sourceLoader ? await sourceLoader(url, signal) : await this.fetchTile(url, signal);
      if (signal.aborted || this.disposed) { source.dispose(); throw new DOMException('Topography load aborted', 'AbortError'); }
      const canvas = document.createElement('canvas'); canvas.width = source.width; canvas.height = source.height;
      const context = canvas.getContext('2d', { willReadFrequently: true })!;
      let packed: Uint8ClampedArray;
      try { context.drawImage(source.image, 0, 0); packed = context.getImageData(0, 0, canvas.width, canvas.height).data; }
      catch (error) { canvas.width = canvas.height = 0; throw error; }
      finally { source.dispose(); }
      let revision = -1;
      const repaint = () => { context.putImageData(new ImageData(colorizeTopography(packed, this.palette, this.options.hillshade === true || this.options.style === 'hillshade'), canvas.width, canvas.height), 0, 0); revision = this.revision; };
      repaint();
      return { width: canvas.width, height: canvas.height, get image() {
        if (revision !== self.revision) { if (self.remaining > 0) { self.remaining--; repaint(); } else self.needsPaint = true; }
        return canvas;
      }, dispose() { canvas.width = canvas.height = 0; } };
    };
    const self = this;
    // Packed bytes and the display canvas each consume RGBA storage. Halving the
    // inherited cache budget accounts for both without maintaining another cache.
    this.renderer = new PyramidRenderer(descriptor.tiles, loader, redraw, this.tileBudget, undefined, true);
  }
  private async fetchTile(url: string, signal: AbortSignal): Promise<TileImage> {
    const response = await fetch(url, { signal }); if (!response.ok) throw new Error(`Topography tile request failed (${response.status})`);
    const image = await createImageBitmap(await response.blob(), { premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
    return { image, width: image.width, height: image.height, dispose: () => image.close() };
  }
  setStyle(style: TopoStyle, strength: number, hillshade = false) {
    if (!['plain', 'lowlands-dark', 'elevation-colors', 'hillshade'].includes(style) || !Number.isFinite(strength)) throw new TypeError('Invalid topography style');
    const next = { style, strength: clamp(strength), hillshade };
    if (next.style === this.options.style && next.strength === this.options.strength && !!next.hillshade === !!this.options.hillshade) return;
    const paletteChanged = next.style !== this.options.style || !!next.hillshade !== !!this.options.hillshade;
    this.options = next;
    if (paletteChanged) { this.palette = topographyPalette(this.descriptor, { ...next, strength: 1 }); this.revision++; }
    this.redraw();
  }
  update(view: RasterView) { if (this.options.strength > 0 && (this.options.style !== 'plain' || this.options.hillshade)) this.renderer.update(topographySamplingView(view, this.descriptor.tiles, this.tileBudget)); }
  draw(context: CanvasRenderingContext2D, view: RasterView) {
    if (this.options.style === 'plain' && !this.options.hillshade || this.options.strength === 0) return;
    if (!this.layer) this.layer = document.createElement('canvas');
    const scale = Math.min(1, this.maxLayerEdge / Math.max(view.screenWidth, view.screenHeight));
    const width = Math.max(1, Math.ceil(view.screenWidth * scale)), height = Math.max(1, Math.ceil(view.screenHeight * scale));
    if (this.layer.width !== width || this.layer.height !== height) { this.layer.width = width; this.layer.height = height; }
    const layerContext = this.layer.getContext('2d')!;
    layerContext.setTransform(1, 0, 0, 1, 0, 0); layerContext.clearRect(0, 0, width, height);
    layerContext.setTransform(scale, 0, 0, scale, 0, 0);
    this.remaining = 2; this.needsPaint = false; this.renderer.draw(layerContext, view);
    // Resolve ancestor fades at full opacity; the user's strength is applied once.
    context.save(); context.globalAlpha *= this.options.strength;
    context.drawImage(this.layer, 0, 0, width, height, 0, 0, view.screenWidth, view.screenHeight); context.restore();
    if (this.needsPaint) this.redraw();
  }
  get stats() { const stats = this.renderer.stats; return { ...stats, bytes: stats.bytes * 2 + (this.layer ? this.layer.width * this.layer.height * 4 : 0) }; }
  destroy() { this.disposed = true; this.renderer.destroy(); if (this.layer) this.layer.width = this.layer.height = 0; this.layer = undefined; }
}
