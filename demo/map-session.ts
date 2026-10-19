import { createMapViewer, loadMapCatalog } from "../src/index.ts";
import type { MapDefinition, MapViewer, ViewState } from "../src/index.ts";
import type { Settings } from "./ui/store";
import { parseWaterPreview, preferPublishedWater, type WaterPreview } from "./water-preview";

import { loadVariants, ORIGINAL, type Variant } from "./map-variants";
export { ORIGINAL, type Variant } from "./map-variants";
const message = (error: unknown) => (error instanceof Error ? error.message : String(error));
export class MapSession {
  viewer: MapViewer | null = null;
  maps: MapDefinition[] = [];
  variants = new Map<string, Variant[]>();
  current: MapDefinition | null = null;
  variantId = ORIGINAL;
  loading = false;
  loadingVariantId: string | null = null;
  extendedZoom = false;
  retryLoad: (() => Promise<void>) | null = null;
  private token = 0;
  private waterPreview: WaterPreview | null = null;
  private previewFailed = false;
  private previewRefreshing = false;
  readonly waterPreviewEnabled = new URLSearchParams(location.search).get("waterPreview") === "1";
  onChange: () => void = () => {};
  onViewChange: (state: ViewState) => void = () => {};
  onPointerMove: (
    point: { x: number; z: number; insideMap: boolean; gridReference: string | null } | null,
  ) => void = () => {};
  onLoading: (title: string, detail: string, error: boolean) => void = () => {};
  constructor(
    private canvas: HTMLCanvasElement,
    private settings: Settings,
  ) {}
  get activeVariant(): Variant | null {
    return this.variants.get(this.current?.id ?? "")?.find((v) => v.id === this.variantId) ?? null;
  }
  get viewOptions(): Variant[] {
    return this.current
      ? [
          { id: ORIGINAL, label: "Builtin", image: this.current.image, kind: "builtin" },
          ...(this.variants.get(this.current.id) ?? []),
        ]
      : [];
  }
  get waterStatus(): string {
    if (!this.waterPreviewEnabled || this.variantId !== "satellite") return "";
    const family = this.current?.id.startsWith("ignus-") ? "ignus" : "heartland";
    const progress = this.waterPreview?.maps[family];
    const label = !progress
      ? "Partial water preview unavailable · accepted imagery shown"
      : progress.scopeCount === null
        ? "Partial preview · no new water tiles on this map yet"
        : `Partial preview · ${progress.committedCount.toLocaleString()} verified water tiles · ` +
          `${progress.remainingCount!.toLocaleString()} pending in this water scope`;
    return label + (this.previewFailed ? " · refresh failed" : "");
  }
  async start(): Promise<void> {
    this.viewer?.destroy();
    this.viewer = null;
    this.onLoading("Starting map engine", "Initializing WASM…", false);
    try {
      this.viewer = await createMapViewer(this.canvas, {
        maxZoom: 400,
        mode: "builtin",
        grid: this.settings.grid,
        gridLabels: this.settings.labels,
        tint: this.settings.tint,
        contourInterval: this.settings.contourInterval,
        contourSmoothingMeters: this.settings.contourSmoothingMeters,
        topoStyle: this.settings.topoStyle,
        topoStrength: this.settings.topoStrength,
        topoHillshade: this.settings.topoHillshade,
        onViewChange: (state) => this.onViewChange(state),
        onPointerMove: (point) => this.onPointerMove(point),
      });
    } catch (error) {
      this.onLoading("Map engine failed to start", message(error), true);
      return;
    }
    this.onLoading("Loading map catalog", "/maps/manifest.json", false);
    try {
      this.maps = (await loadMapCatalog("/maps/manifest.json")).maps;
    } catch (error) {
      const missing = /\(404\)/.test(message(error));
      this.onLoading(
        missing ? "No local map installed" : "Could not read map catalog",
        missing
          ? "Expected demo/public/maps/manifest.json. Run the extractor to create it."
          : message(error),
        !missing,
      );
      return;
    }
    if (!this.maps.length) {
      this.onLoading(
        "No local map installed",
        "The catalog in demo/public/maps/manifest.json lists no maps.",
        false,
      );
      return;
    }
    await this.loadVariants();
    await this.refreshWaterPreview(true);
    this.onChange();
    if (this.waterPreviewEnabled)
      setInterval(() => {
        if (!document.hidden) void this.refreshWaterPreview();
      }, 30000);
  }
  async showMap(
    map: MapDefinition,
    variantId: string,
    restore?: { centerX?: number; centerZ?: number; zoom?: number },
  ): Promise<void> {
    if (!this.viewer) return;
    this.retryLoad = () => this.showMap(map, variantId, restore);
    const variant = this.variants.get(map.id)?.find((v) => v.id === variantId);
    const sameMap = this.current?.id === map.id;
    const token = ++this.token;
    this.loading = true;
    this.loadingVariantId = variant?.id ?? ORIGINAL;
    this.onChange();
    this.onLoading(`Loading ${map.name}`, variant?.label ?? "Builtin", false);
    try {
      await this.viewer.setMap(
        variant
          ? {
              ...map,
              image: variant.image,
              tiles: variant.tiles,
              contours: variant.contours,
              topography: variant.topography,
            }
          : map,
        { preserveView: sameMap },
      );
      if (token !== this.token) return;
      this.viewer.setMode(variant?.kind === "terrain" ? "raw" : "builtin");
      this.current = map;
      this.variantId = variant?.id ?? ORIGINAL;
      if (restore) this.viewer.setView(restore);
      this.onLoading("", "", false);
      this.onChange();
    } catch (error) {
      if (token !== this.token) return;
      this.onLoading(`Could not load ${variant?.label ?? map.name}`, message(error), true);
    } finally {
      if (token === this.token) {
        this.loading = false;
        this.loadingVariantId = null;
        this.onChange();
      }
    }
  }
  selectMap(
    id: string,
    variant?: string,
    restore?: { centerX?: number; centerZ?: number; zoom?: number },
  ) {
    const map = this.maps.find((item) => item.id === id);
    return map
      ? this.showMap(map, variant ?? (this.waterPreviewEnabled ? "satellite" : ORIGINAL), restore)
      : Promise.resolve();
  }
  private async loadVariants(signal?: AbortSignal): Promise<void> {
    const next = await loadVariants(this.maps, signal);
    if (next) this.variants = next;
  }
  async refreshWaterPreview(initial = false): Promise<void> {
    if (!this.waterPreviewEnabled || this.loading || this.previewRefreshing) return;
    this.previewRefreshing = true;
    const controller = new AbortController(),
      timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch("/maps/candidate-v8/incremental/coverage.json", {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("Published water snapshot unavailable");
      const declared = Number(response.headers.get("Content-Length"));
      if (!Number.isSafeInteger(declared) || declared < 0 || declared > 256 * 1024)
        throw new Error("Water metadata cap");
      const next = parseWaterPreview(await response.json());
      await this.loadVariants(controller.signal);
      for (const [id, list] of this.variants) {
        const family = id.startsWith("ignus-")
          ? "ignus"
          : id.startsWith("heartland-")
            ? "heartland"
            : null;
        const satellite = list.find((v) => v.id === "satellite");
        if (family && satellite?.tiles)
          satellite.tiles = preferPublishedWater(satellite.tiles, next.maps[family]);
      }
      this.waterPreview = next;
      this.previewFailed = false;
      if (!initial && this.current && this.variantId === "satellite" && !this.loading) {
        const satellite = this.variants.get(this.current.id)?.find((v) => v.id === "satellite");
        if (satellite?.tiles) this.viewer?.updateRasterTiles(satellite.tiles);
      }
    } catch {
      this.previewFailed = true;
    } finally {
      clearTimeout(timer);
      this.previewRefreshing = false;
      this.onChange();
    }
  }
}
