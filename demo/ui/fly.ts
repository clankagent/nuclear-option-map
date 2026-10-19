import type { MapViewer } from "../../src/viewer";
export class FlyController {
  private frame = 0;
  constructor(private viewer: () => MapViewer | null) {}
  cancel = () => {
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = 0;
  };
  to(target: { x: number; z: number; zoom?: number }, ms = 560) {
    this.cancel();
    const viewer = this.viewer();
    if (!viewer) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches || ms <= 0) {
      viewer.setView({ centerX: target.x, centerZ: target.z, zoom: target.zoom });
      return;
    }
    const from = viewer.state,
      start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / ms),
        ease = t * t * (3 - 2 * t);
      viewer.setView({
        centerX: from.centerX + (target.x - from.centerX) * ease,
        centerZ: from.centerZ + (target.z - from.centerZ) * ease,
        zoom: target.zoom === undefined ? undefined : from.zoom + (target.zoom - from.zoom) * ease,
      });
      this.frame = t < 1 ? requestAnimationFrame(tick) : 0;
    };
    this.frame = requestAnimationFrame(tick);
  }
}
