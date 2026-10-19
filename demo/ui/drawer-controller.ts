import { $ } from "./dom";
import type { AppContext } from "./app-context";
import { renderPanel, type PanelKind } from "./panels";
import { DEFAULT_SETTINGS } from "./store";

export class DrawerController {
  private drawerAnimation: Animation | null = null;
  constructor(private app: AppContext) {}
  animateDrawer(closing = false) {
    this.drawerAnimation?.cancel();
    this.drawerAnimation = null;
    const drawer = $("drawer");
    drawer.inert = closing;
    if (matchMedia("(prefers-reduced-motion:reduce)").matches) {
      drawer.hidden = closing;
      return;
    }
    const offset = matchMedia("(max-width:899px)").matches ? "translateY(12px)" : "translateX(8px)";
    const frames = [
      { opacity: 0, transform: offset },
      { opacity: 1, transform: "none" },
    ];
    const animation = drawer.animate(closing ? [...frames].reverse() : frames, {
      duration: 150,
      easing: "ease-out",
    });
    this.drawerAnimation = animation;
    animation.onfinish = () => {
      if (this.drawerAnimation === animation) {
        drawer.hidden = closing;
        this.drawerAnimation = null;
      }
    };
  }

  syncDrawerButtons() {
    this.app.root.classList.toggle("panel-open", this.app.drawer !== null);
    for (const [id, kind] of [
      ["settings-open", "settings"],
      ["places-open", "places"],
      ["layers-open", "layers"],
    ])
      $(id).setAttribute("aria-expanded", String(this.app.drawer === kind));
  }

  openDrawer(kind: PanelKind) {
    const opening = !this.app.drawer;
    this.app.drawer = kind;
    $("drawer").hidden = false;
    $("drawer").inert = false;
    $("drawer-title").textContent =
      kind === "pin"
        ? "Pin details"
        : kind === "measure"
          ? "Measurement"
          : kind[0].toUpperCase() + kind.slice(1);
    $("sheet-backdrop").hidden = matchMedia("(min-width:900px)").matches;
    this.renderDrawer();
    this.syncDrawerButtons();
    if (opening) this.animateDrawer();
  }

  closeDrawer() {
    if (!this.app.drawer) return;
    this.app.drawer = null;
    this.animateDrawer(true);
    $("sheet-backdrop").hidden = true;
    this.syncDrawerButtons();
    this.app.canvas.focus({ preventScroll: true });
  }

  renderDrawer() {
    if (!this.app.drawer) return;
    renderPanel(this.app.drawer, $("drawer-body"), {
      session: this.app.session,
      settings: this.app.settings,
      state: this.app.state,
      pins: this.app.pins,
      onSettings: () => this.app.settingsController.applySettings(),
      onPin: (pin) => {
        this.app.undo.push({ kind: "pins", before: [...this.app.pins] });
        this.app.pins = this.app.pins.map((p) => (p.id === pin.id ? pin : p));
        this.app.pinsController.saveCurrentPins();
      },
      onDelete: (id) => this.app.pinsController.deleteSelected(id),
      onMeasureFrom: (point) => {
        this.app.measure.pushMeasureUndo();
        this.app.state = {
          ...this.app.state,
          points: [{ x: point.x, z: point.z }],
          finished: false,
        };
        this.app.measure.setTool("measure");
        this.app.view.syncOverlay();
      },
      onFly: (point) => {
        this.app.fly.to(point);
        this.closeDrawer();
      },
      onExport: () => this.app.pinsController.exportPinFile(),
      onImport: (file) => void this.app.pinsController.importPinFile(file),
      onCopy: (value) => void this.app.clipboard.copyText(value),
      onUndoPoint: () => this.app.measure.undoMeasurePoint(),
      onClearMeasure: () => {
        this.app.measure.pushMeasureUndo();
        this.app.state = { ...this.app.state, points: [], finished: false };
        this.app.view.syncOverlay();
        this.renderDrawer();
      },
      onFinishMeasure: () => this.app.measure.finishMeasurement(),
      onView: (id) => void this.app.catalog.switchView(id),
      onShortcuts: () => this.app.showShortcuts(),
      onCopyLink: () => void this.app.permalink.copyLink(),
      onReset: () => {
        Object.assign(this.app.settings, DEFAULT_SETTINGS);
        this.app.session.extendedZoom = false;
        this.app.session.viewer?.setMaxZoom(400);
        this.app.settingsController.applySettings();
        this.renderDrawer();
      },
    });
  }

  bind() {
    const toggleDrawer = (kind: PanelKind) =>
      this.app.drawer === kind ? this.closeDrawer() : this.openDrawer(kind);
    $("places-open").onclick = () => toggleDrawer("places");
    $("layers-open").onclick = () => toggleDrawer("layers");
    $("settings-open").onclick = () => toggleDrawer("settings");
    $("drawer-close").onclick = () => this.closeDrawer();
    $("sheet-backdrop").onclick = () => this.closeDrawer();
    const handle = $("sheet-handle");
    let sheetStart: number | null = null;
    handle.onpointerdown = (event) => {
      sheetStart = event.clientY;
      handle.setPointerCapture(event.pointerId);
    };
    handle.onpointerup = (event) => {
      if (sheetStart !== null && event.clientY - sheetStart > 40) this.closeDrawer();
      sheetStart = null;
    };
    handle.onpointercancel = () => {
      sheetStart = null;
    };
    handle.onclick = () => this.closeDrawer();
    const responsiveLayout = () => {
      const desktop = matchMedia("(min-width:900px)").matches;
      $("sheet-backdrop").hidden = !this.app.drawer || desktop;
      const parent = document.querySelector(desktop ? ".bottom-left" : ".bottom-center")!;
      parent.prepend($("scale-bar"));
    };
    responsiveLayout();
    matchMedia("(min-width:900px)").addEventListener("change", responsiveLayout);
    new ResizeObserver((entries) => {
      const height = entries[0]?.contentRect.height ?? 40;
      document.documentElement.style.setProperty("--bottom-stack-height", `${height}px`);
    }).observe(document.querySelector(".bottom-center")!);
  }
}
