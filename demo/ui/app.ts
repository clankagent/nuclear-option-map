import { AppContext } from "./app-context";
import { updateScaleBar } from "./scale-bar";
import { $ } from "./dom";

/** Boots the demo and connects map events to the UI controllers. */
export class DemoApp {
  private readonly app = new AppContext();
  constructor() {
    this.app.session.onChange = () => this.app.view.syncSession();
    this.app.session.onViewChange = (state) => {
      this.app.overlay.schedule();
      this.app.minimap.update(
        this.app.session.current,
        this.app.session.activeVariant?.image ?? this.app.session.current?.image ?? null,
        this.app.settings.minimap,
        this.app.settings.minimapCollapsed,
      );
      updateScaleBar(state.scale, this.app.settings.units, $("scale-line"), $("scale-label"));
      $("zoom-badge").textContent = `${state.zoom.toFixed(2)}×`;
      const chip = $("interval-chip");
      chip.hidden = !state.contourInterval;
      chip.textContent = state.contourInterval ? `${state.contourInterval} m contours` : "";
      this.app.view.updateReadout();
      this.app.permalink.queueHash();
    };
    this.app.session.onPointerMove = (point) => {
      this.app.readoutPoint = point;
      this.app.view.updateReadout();
    };
    this.app.session.onLoading = (title, detail, error) =>
      this.app.chrome.showLoading(title, detail, error);
  }
  async start() {
    const app = this.app;
    app.chrome.fillIcons();
    app.pointer.bind();
    app.measure.bind();
    app.drawerController.bind();
    app.chrome.bind();
    app.catalog.bind();
    app.search.bind();
    app.keyboard.bind();
    await app.permalink.initializeSession();
    void app.chrome.refreshStatus();
    setInterval(() => {
      if (!document.hidden) void app.chrome.refreshStatus();
    }, 60000);
  }
}
