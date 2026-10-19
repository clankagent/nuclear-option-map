import type { AppContext } from "./app-context";
import {
  exportPins,
  loadPins,
  mergePins,
  newPinId,
  parsePinImport,
  savePins,
  type Pin,
} from "./store";
import type { Point } from "./tools";
import type { SharedPin } from "./permalink";

export class PinsController {
  private pinsMapId: string | null = null;
  get mapId() {
    return this.pinsMapId;
  }
  constructor(private app: AppContext) {}
  loadCurrentPins() {
    this.pinsMapId = this.app.session.current?.id ?? null;
    this.app.pins = this.app.session.current ? loadPins(this.app.session.current.id) : [];
    this.app.state = { ...this.app.state, selectedPin: null, points: [], finished: false };
    this.app.undo.clear();
    this.app.view.syncOverlay();
  }

  saveCurrentPins() {
    if (this.app.session.current) savePins(this.app.session.current.id, this.app.pins);
    this.app.view.syncOverlay();
    if (this.app.drawer) this.app.drawerController.renderDrawer();
  }

  dropPin(point: Point, keepTool = false) {
    if (!this.app.pointer.inBounds(point)) return;
    if (this.app.pins.length >= 200) {
      this.app.toasts.show("This map already has 200 pins");
      return;
    }
    this.app.undo.push({ kind: "pins", before: [...this.app.pins] });
    const pin: Pin = {
      id: newPinId(),
      name: `Pin ${this.app.pins.length + 1}`,
      x: point.x,
      z: point.z,
      color: "green",
    };
    this.app.pins = [...this.app.pins, pin];
    this.app.state = { ...this.app.state, selectedPin: pin.id };
    this.saveCurrentPins();
    if (!keepTool) this.app.measure.setTool("navigate");
    this.app.drawerController.openDrawer("pin");
  }

  deleteSelected(id = this.app.state.selectedPin ?? "") {
    const pin = this.app.pins.find((p) => p.id === id);
    if (!pin) return;
    this.app.undo.push({ kind: "pins", before: [...this.app.pins] });
    this.app.pins = this.app.pins.filter((p) => p.id !== pin.id);
    this.app.state = { ...this.app.state, selectedPin: null };
    this.saveCurrentPins();
    if (this.app.drawer === "pin") this.app.drawerController.closeDrawer();
    this.app.toasts.show("Pin deleted", { label: "Undo", run: () => this.app.history.undoLast() });
  }

  offerSharedPins(shared: SharedPin[]) {
    const valid = shared.filter((p) => this.app.pointer.inBounds(p));
    if (!valid.length) return;
    this.app.toasts.show(`${valid.length} shared pin${valid.length === 1 ? "" : "s"}`, {
      label: "Add shared pins",
      run: () => {
        this.app.undo.push({ kind: "pins", before: [...this.app.pins] });
        this.app.pins = mergePins(
          this.app.pins,
          valid.map((p) => ({ ...p, id: newPinId(), color: "green" })),
        );
        this.saveCurrentPins();
        this.app.toasts.show("Shared pins added");
      },
    });
  }

  exportPinFile() {
    const maps = Object.fromEntries(
      this.app.session.maps.map((m) => [
        m.id,
        m.id === this.app.session.current?.id ? this.app.pins : loadPins(m.id),
      ]),
    );
    const blob = new Blob([exportPins(maps)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "nuclear-option-map-pins.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  async importPinFile(file: File) {
    const result = parsePinImport(await file.text(), this.app.session.maps);
    if (!result.ok) {
      this.app.toasts.show(result.error);
      return;
    }
    for (const [mapId, incoming] of Object.entries(result.maps)) {
      const existing = mapId === this.app.session.current?.id ? this.app.pins : loadPins(mapId);
      if (mapId === this.app.session.current?.id) {
        this.app.undo.push({ kind: "pins", before: [...this.app.pins] });
        this.app.pins = mergePins(existing, incoming);
      } else savePins(mapId, mergePins(existing, incoming));
    }
    this.saveCurrentPins();
    this.app.toasts.show("Pins imported");
  }
}
