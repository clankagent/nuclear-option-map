import type { MapDefinition } from "../../src/catalog";
import type { MapViewer } from "../../src/viewer";
export class Minimap {
  private map: MapDefinition | null = null;
  constructor(
    private root: HTMLElement,
    private surface: HTMLElement,
    private rectangle: HTMLElement,
    private viewer: () => MapViewer | null,
  ) {
    const move = (event: PointerEvent) => {
      if (!this.map || !this.viewer()) return;
      const box = this.surface.getBoundingClientRect(),
        nx = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)),
        ny = Math.max(0, Math.min(1, (event.clientY - box.top) / box.height));
      this.viewer()!.setView({
        centerX: (nx - 0.5) * this.map.widthMeters,
        centerZ: (0.5 - ny) * this.map.heightMeters,
      });
    };
    this.surface.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      this.surface.setPointerCapture(event.pointerId);
      move(event);
    });
    this.surface.addEventListener("pointermove", (event) => {
      if (this.surface.hasPointerCapture(event.pointerId)) move(event);
    });
    this.surface.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        this.viewer()?.fit();
      }
    });
  }
  update(map: MapDefinition | null, image: string | null, enabled: boolean, collapsed: boolean) {
    this.map = map;
    this.root.hidden = !map || !enabled;
    this.root.dataset.collapsed = String(collapsed);
    const toggle = this.root.querySelector<HTMLButtonElement>("button");
    if (toggle) {
      toggle.setAttribute("aria-expanded", String(!collapsed));
      toggle.setAttribute("aria-label", collapsed ? "Expand overview" : "Collapse overview");
      toggle.textContent = collapsed ? "+" : "−";
    }
    if (!map) return;
    this.surface.style.aspectRatio = String(map.widthMeters / map.heightMeters);
    this.surface.style.backgroundImage = image ? `url("${image.replace(/"/g, "%22")}")` : "";
    const viewer = this.viewer();
    if (!viewer) return;
    const s = viewer.state;
    const width = Math.min(1, s.width / s.scale / map.widthMeters),
      height = Math.min(1, s.height / s.scale / map.heightMeters);
    this.rectangle.style.left = `${Math.max(0, Math.min(1 - width, 0.5 + s.centerX / map.widthMeters - width / 2)) * 100}%`;
    this.rectangle.style.top = `${Math.max(0, Math.min(1 - height, 0.5 - s.centerZ / map.heightMeters - height / 2)) * 100}%`;
    this.rectangle.style.width = `${width * 100}%`;
    this.rectangle.style.height = `${height * 100}%`;
  }
}
