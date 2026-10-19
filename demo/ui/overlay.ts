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

/** Chart symbol colors; each reads against both the light topographic and dark imagery views. */
export const PIN_HEX: Record<Pin["color"], string> = {
  green: "#1f8a4c",
  amber: "#c98a00",
  red: "#c8312f",
  blue: "#2463c9",
  white: "#f4f1ea",
};

/** Flight-plan magenta, as used for courses on aeronautical charts. */
const ROUTE = "#c8177a";
const HALO = "rgba(250, 248, 242, 0.94)";
const INK = "#16140f";
const FONT = '600 11.5px Bahnschrift, "DIN Alternate", "Roboto Condensed", "Arial Narrow", sans-serif';

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
    ctx.font = FONT;
    const screen = (p: Point) => viewer.worldToScreen(p.x, p.z);
    this.paintRoute(ctx, this.points.map(screen));
    this.paintPins(ctx, viewer, w, h, screen);
    if (this.hover) {
      const p = screen(this.hover);
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = ROUTE;
      ctx.lineWidth = 1.25;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 11, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  /** Cased label: a light halo under dark or colored lettering, without a box. */
  private label(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, color: string) {
    ctx.lineWidth = 3.5;
    ctx.strokeStyle = HALO;
    ctx.strokeText(text, x, y);
    ctx.fillStyle = color;
    ctx.fillText(text, x, y);
  }

  private paintRoute(ctx: CanvasRenderingContext2D, pts: { x: number; y: number }[]) {
    if (!pts.length) return;
    const trace = () => {
      ctx.beginPath();
      pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
    };
    trace();
    ctx.strokeStyle = HALO;
    ctx.lineWidth = 5;
    ctx.stroke();
    trace();
    ctx.strokeStyle = ROUTE;
    ctx.lineWidth = 2;
    ctx.stroke();

    const legText = (i: number) =>
      `${formatDistance(distanceMeters(this.points[i - 1], this.points[i]), this.units)}` +
      `  ${formatBearing(bearingDegrees(this.points[i - 1], this.points[i]))}`;
    const candidates: { x: number; y: number; width: number; height: number; id: string }[] = [];
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1],
        b = pts[i];
      if (Math.hypot(b.x - a.x, b.y - a.y) < 40) continue;
      candidates.push({
        id: String(i),
        x: (a.x + b.x) / 2 + 8,
        y: (a.y + b.y) / 2 - 18,
        width: ctx.measureText(legText(i)).width + 6,
        height: 16,
      });
    }
    ctx.textAlign = "left";
    for (const placed of placeLabels(candidates))
      this.label(ctx, legText(Number(placed.id)), placed.x + 3, placed.y + 8, ROUTE);

    pts.forEach((p, i) => {
      const end = i === pts.length - 1 && this.finished;
      ctx.beginPath();
      ctx.arc(p.x, p.y, end || i === 0 ? 4.5 : 3.5, 0, Math.PI * 2);
      ctx.fillStyle = i === 0 || end ? ROUTE : HALO;
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = i === 0 || end ? HALO : ROUTE;
      ctx.stroke();
    });
  }

  private paintPins(
    ctx: CanvasRenderingContext2D,
    viewer: MapViewer,
    w: number,
    h: number,
    screen: (p: Point) => { x: number; y: number },
  ) {
    const labels = this.pins.map((pin) => {
      const p = screen(pin);
      return {
        id: pin.id,
        x: p.x + 9,
        y: p.y - 8,
        width: ctx.measureText(pin.name).width + 6,
        height: 16,
        priority: pin.id === this.selected ? 1 : 0,
      };
    });
    const visible = new Set(placeLabels(labels).map((label) => label.id));
    ctx.textAlign = "left";
    this.pins.forEach((pin) => {
      const p = screen(pin);
      if (p.x < -20 || p.y < -20 || p.x > w + 20 || p.y > h + 20) return;
      const selected = pin.id === this.selected;
      // Chart point symbol: a ringed dot; selection adds an outer ring.
      if (selected) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 10, 0, Math.PI * 2);
        ctx.lineWidth = 3.5;
        ctx.strokeStyle = HALO;
        ctx.stroke();
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = INK;
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
      ctx.fillStyle = PIN_HEX[pin.color];
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = pin.color === "white" ? INK : HALO;
      ctx.stroke();
      if (visible.has(pin.id) && (viewer.state.zoom >= 1.5 || selected)) {
        const label = labels.find((item) => item.id === pin.id)!;
        this.label(ctx, pin.name, label.x + 3, label.y + 8, INK);
      }
    });
  }
}
