import type { AppContext } from "./app-context";
import {
  exportPins,
  loadPins,
  mergePins,
  parsePinImport,
  savePins,
} from "./store";

export class PinsController {
  private pinsMapId: string | null = null;
  get mapId() {
    return this.pinsMapId;
  }
  constructor(private app: AppContext) {}
  loadCurrentPins() {
    this.pinsMapId = this.app.session.current?.id ?? null;
    this.app.pins = this.app.session.current ? loadPins(this.app.session.current.id) : [];
  }

  saveCurrentPins() {
    if (this.app.session.current) savePins(this.app.session.current.id, this.app.pins);
    if (this.app.drawer) this.app.drawerController.renderDrawer();
  }

  deleteSelected(id?: string) {
    const pin = this.app.pins.find((p) => p.id === id);
    if (!pin) return;
    this.app.pins = this.app.pins.filter((p) => p.id !== pin.id);
    this.saveCurrentPins();
    const mapId = this.mapId;
    this.app.toasts.show("Pin deleted", {
      label: "Undo",
      run: () => {
        if (mapId !== this.mapId) return;
        this.app.pins = mergePins(this.app.pins, [pin]);
        this.saveCurrentPins();
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
        this.app.pins = mergePins(existing, incoming);
      } else savePins(mapId, mergePins(existing, incoming));
    }
    this.saveCurrentPins();
    this.app.toasts.show("Pins imported");
  }
}
