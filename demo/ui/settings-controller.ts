import type { AppContext } from "./app-context";
import { saveSettings } from "./store";

export class SettingsController {
  constructor(private app: AppContext) {}
  applySettings() {
    const v = this.app.session.viewer;
    if (v) {
      v.setGrid(this.app.settings.grid);
      v.setGridLabels(this.app.settings.labels);
      if (this.app.session.variantId === "original") v.setTint(this.app.settings.tint);
      v.setContourSmoothing(this.app.settings.contourSmoothingMeters);
      v.setTopoStyle(
        this.app.settings.topoStyle,
        this.app.settings.topoStrength,
        this.app.settings.topoHillshade,
      );
      if (v.state.map?.contours)
        void v
          .setContourInterval(this.app.settings.contourInterval)
          .catch(() => this.app.toasts.show("Could not load contours"));
    }
    saveSettings(this.app.settings);
    this.app.view.syncOverlay();
    if (v) this.app.session.onViewChange(v.state);
  }
}
