import { $ } from "./dom";
import type { AppContext } from "./app-context";
import { gridReference } from "../../src/catalog";
import { formatCoordinate, formatDistance } from "./geo";

export class ViewController {
  constructor(private app: AppContext) {}
  syncSession() {
    if (this.app.session.current && this.app.pinsController.mapId !== this.app.session.current.id) {
      this.app.pinsController.loadCurrentPins();
      if (this.app.drawer === "pin" || this.app.drawer === "measure")
        this.app.drawerController.closeDrawer();
    }
    $("map-name").textContent = this.app.session.current?.name ?? "Choose map";
    $("map-switcher").setAttribute(
      "aria-label",
      `Map: ${this.app.session.current?.name ?? "choose map"}, switch map`,
    );
    const preview = $("water-preview");
    preview.hidden = !this.app.session.waterStatus;
    $("water-preview-status").textContent = this.app.session.waterStatus;
    this.app.catalog.renderMaps();
    this.app.catalog.renderViewCards();
    this.app.minimap.update(
      this.app.session.current,
      this.app.session.activeVariant?.image ?? this.app.session.current?.image ?? null,
      this.app.settings.minimap,
      this.app.settings.minimapCollapsed,
    );
    if (this.app.drawer) this.app.drawerController.renderDrawer();
    this.syncOverlay();
  }

  syncOverlay() {
    this.app.overlay.pins = this.app.pins;
    this.app.overlay.points = this.app.state.points;
    this.app.overlay.selected = this.app.state.selectedPin;
    this.app.overlay.finished = this.app.state.finished;
    this.app.overlay.units = this.app.settings.units;
    this.app.overlay.schedule();
  }

  updateReadout() {
    const s = this.app.session.viewer?.state;
    if (!s || !this.app.session.current) return;
    const p = this.app.readoutPoint ?? {
      x: s.centerX,
      z: s.centerZ,
      gridReference: gridReference(this.app.session.current, s.centerX, s.centerZ),
    };
    $("readout").textContent =
      `${p.gridReference ?? "—"} · x ${formatCoordinate(p.x)} · z ${formatCoordinate(p.z)}` +
      ` · 1 px = ${formatDistance(1 / s.scale, this.app.settings.units)}`;
  }
}
