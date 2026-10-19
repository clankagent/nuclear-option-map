import { $ } from "./dom";
import type { AppContext } from "./app-context";
import { gridReference } from "../../src/catalog";
import { formatCoordinate, formatDistance } from "./geo";

export class ViewController {
  constructor(private app: AppContext) {}
  syncSession() {
    this.app.rulers.syncMap();
    if (this.app.session.current && this.app.pinsController.mapId !== this.app.session.current.id) {
      this.app.pinsController.loadCurrentPins();
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
  }

  updateReadout() {
    const s = this.app.session.viewer?.state;
    if (!s || !this.app.session.current) return;
    const p = this.app.readoutPoint ?? {
      x: s.centerX,
      z: s.centerZ,
      gridReference: gridReference(this.app.session.current, s.centerX, s.centerZ),
    };
    const field = (name: string, value: string, className = "") => {
      const node = document.createElement("span");
      node.className = `readout-field ${className}`.trim();
      const label = document.createElement("small");
      label.textContent = name;
      node.append(label, value);
      return node;
    };
    $("readout").replaceChildren(
      field("Grid", p.gridReference ?? "—", "grid"),
      field("X", formatCoordinate(p.x)),
      field("Z", formatCoordinate(p.z)),
      field("Px", formatDistance(1 / s.scale, this.app.settings.units), "secondary"),
    );
  }
}
