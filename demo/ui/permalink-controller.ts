import type { AppContext } from "./app-context";
import { buildHash, parseHash } from "./permalink";

export class PermalinkController {
  private lastHashTime = 0;
  private hashTimer = 0;
  private restoringHash = false;
  constructor(private app: AppContext) {}
  async initializeSession() {
    await this.app.session.start();
    const parsed = parseHash(location.hash),
      map = this.app.session.maps.find((m) => m.id === parsed.map) ?? this.app.session.maps[0];
    if (!map) return;
    this.restoringHash = true;
    this.enableLinkedZoom(parsed.zoom);
    await this.app.session.selectMap(map.id, parsed.variant, {
      centerX: parsed.centerX,
      centerZ: parsed.centerZ,
      zoom: parsed.zoom,
    });
    this.restoringHash = false;
    this.app.view.syncSession();
    this.queueHash();
    if (parsed.sharedPins?.length) this.app.pinsController.offerSharedPins(parsed.sharedPins);
  }

  queueHash() {
    if (this.restoringHash || !this.app.session.current || !this.app.session.viewer) return;
    clearTimeout(this.hashTimer);
    const delay = Math.max(400, this.lastHashTime + 400 - performance.now());
    this.hashTimer = window.setTimeout(() => {
      const s = this.app.session.viewer!.state;
      const shared = parseHash(location.hash).sharedPins;
      const hash = buildHash({
        map: this.app.session.current!.id,
        variant: this.app.session.variantId,
        centerX: s.centerX,
        centerZ: s.centerZ,
        zoom: s.zoom,
        sharedPins: shared,
      });
      if (location.hash !== hash) history.replaceState(null, "", hash);
      this.lastHashTime = performance.now();
    }, delay);
  }

  async restoreHash() {
    const h = parseHash(location.hash),
      map = this.app.session.maps.find((m) => m.id === h.map);
    if (!map) return;
    this.app.fly.cancel();
    clearTimeout(this.hashTimer);
    this.restoringHash = true;
    this.enableLinkedZoom(h.zoom);
    try {
      if (
        map.id !== this.app.session.current?.id ||
        (h.variant && h.variant !== this.app.session.variantId)
      )
        await this.app.session.selectMap(map.id, h.variant, {
          centerX: h.centerX,
          centerZ: h.centerZ,
          zoom: h.zoom,
        });
      else
        this.app.session.viewer?.setView({ centerX: h.centerX, centerZ: h.centerZ, zoom: h.zoom });
    } finally {
      this.restoringHash = false;
    }
    this.queueHash();
    if (h.sharedPins?.length) this.app.pinsController.offerSharedPins(h.sharedPins);
  }

  enableLinkedZoom(zoom?: number) {
    if (zoom !== undefined && zoom > 400) {
      this.app.session.extendedZoom = true;
      this.app.session.viewer?.setMaxZoom(1024);
    }
  }

  async copyLink() {
    if (!this.app.session.viewer || !this.app.session.current) return;
    const s = this.app.session.viewer.state;
    const sharedPins = this.app.pins.slice(0, 8).map(({ name, x, z }) => ({ name, x, z }));
    const hash = buildHash({
      map: this.app.session.current.id,
      variant: this.app.session.variantId,
      centerX: s.centerX,
      centerZ: s.centerZ,
      zoom: s.zoom,
      sharedPins,
    });
    await this.app.clipboard.copyText(
      `${location.origin}${location.pathname}${location.search}${hash}`,
    );
  }
}
