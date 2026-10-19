import type { MapViewer } from "../../src/viewer";
import type { Pin } from "./store";
import type { Point } from "./tools";
import {
  bearingDegrees,
  distanceMeters,
  formatBearing,
  formatDistance,
  type UnitSystem,
} from "./geo";
import { placeLabels } from "./overlay-layout";
export const PIN_HEX: Record<Pin["color"], string> = {
  green: "#6fd3a8",
  amber: "#f2b84b",
  red: "#ec7774",
  blue: "#81b9ed",
  white: "#e8f1f0",
};
export class Overlay {
  private frame = 0;
  pins: Pin[] = [];
  points: Point[] = [];
  selected: string | null = null;
  finished = false;
  hover: Point | null = null;
  units: UnitSystem = "metric";
  constructor(
    private canvas: HTMLCanvasElement,
    private viewer: () => MapViewer | null,
  ) {}
  schedule = () => {
    if (!this.frame)
      this.frame = requestAnimationFrame(() => {
        this.frame = 0;
        this.paint();
      });
  };
  private paint() {
    const viewer = this.viewer();
    if (!viewer) return;
    const w = viewer.state.width,
      h = viewer.state.height,
      dpr = devicePixelRatio || 1;
    if (this.canvas.width !== Math.round(w * dpr) || this.canvas.height !== Math.round(h * dpr)) {
      this.canvas.width = Math.round(w * dpr);
      this.canvas.height = Math.round(h * dpr);
    }
    const ctx = this.canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.textBaseline = "middle";
    const pill = (x: number, y: number, width: number, height: number) => {
      ctx.beginPath();
      ctx.roundRect(x, y, width, height, 7);
      ctx.fillStyle = "rgba(12,20,24,.82)";
      ctx.fill();
      ctx.strokeStyle = "rgba(186,221,218,.3)";
      ctx.lineWidth = 1;
      ctx.stroke();
    };
    const screen = (p: Point) => viewer.worldToScreen(p.x, p.z);
    const pts = this.points.map(screen);
    if (pts.length) {
      ctx.strokeStyle = "#f2b84b";
      ctx.lineWidth = 2;
      ctx.shadowColor = "#081115";
      ctx.shadowBlur = 6;
      ctx.beginPath();
      pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.stroke();
      ctx.shadowBlur = 0;
      const occupied: { x: number; y: number; width: number; height: number; id: string }[] = [];
      ctx.font = "11px ui-monospace, monospace";
      for (let i = 1; i < pts.length; i++) {
        const a = pts[i - 1],
          b = pts[i],
          text =
            `${formatDistance(distanceMeters(this.points[i - 1], this.points[i]), this.units)}` +
            ` · ${formatBearing(bearingDegrees(this.points[i - 1], this.points[i]))}`;
        occupied.push({
          id: String(i),
          x: (a.x + b.x) / 2 + 6,
          y: (a.y + b.y) / 2 - 28,
          width: ctx.measureText(text).width + 16,
          height: 24,
        });
      }
      ctx.font = "11px ui-monospace, monospace";
      ctx.textBaseline = "middle";
      for (const label of placeLabels(occupied)) {
        const i = Number(label.id),
          text =
            `${formatDistance(distanceMeters(this.points[i - 1], this.points[i]), this.units)}` +
            ` · ${formatBearing(bearingDegrees(this.points[i - 1], this.points[i]))}`;
        pill(label.x, label.y, label.width, label.height);
        ctx.fillStyle = "#f2b84b";
        ctx.fillText(text, label.x + 8, label.y + 12);
      }
      for (const p of pts) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, this.finished ? 5 : 4, 0, Math.PI * 2);
        ctx.fillStyle = "#f2b84b";
        ctx.fill();
        ctx.strokeStyle = "#0a151b";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    }
    ctx.font = "12px system-ui";
    const labels = this.pins.map((pin) => {
      const p = screen(pin);
      return {
        id: pin.id,
        x: p.x + 12,
        y: p.y - 16,
        width: ctx.measureText(pin.name).width + 30,
        height: 24,
        priority: pin.id === this.selected ? 1 : 0,
      };
    });
    const visible = new Set(placeLabels(labels).map((label) => label.id));
    this.pins.forEach((pin) => {
      const p = screen(pin);
      if (p.x < -20 || p.y < -20 || p.x > w + 20 || p.y > h + 20) return;
      const color = PIN_HEX[pin.color];
      ctx.fillStyle = color;
      ctx.strokeStyle = "#061016";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(p.x, p.y - 4, pin.id === this.selected ? 8 : 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      if (visible.has(pin.id) && (viewer.state.zoom >= 1.5 || pin.id === this.selected)) {
        const label = labels.find((label) => label.id === pin.id)!;
        pill(label.x, label.y, label.width, label.height);
        ctx.beginPath();
        ctx.arc(label.x + 10, label.y + 12, 3, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.fillStyle = "#f2f7f5";
        ctx.fillText(pin.name, label.x + 19, label.y + 12);
      }
    });
    if (this.hover) {
      const p = screen(this.hover);
      ctx.beginPath();
      ctx.arc(p.x, p.y, 10, 0, Math.PI * 2);
      ctx.strokeStyle = "#f2b84b88";
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  }
}
