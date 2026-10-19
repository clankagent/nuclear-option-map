import type { AppContext } from "./app-context";
import { gridReference } from "../../src/catalog";
import type { Point } from "./tools";

export class ContextController {
  constructor(private app: AppContext) {}
  openContext(x: number, y: number, point: Point) {
    if (!this.app.session.current || !this.app.pointer.inBounds(point)) return;
    const grid = gridReference(this.app.session.current, point.x, point.z);
    this.app.menu.open(
      x,
      y,
      [
        {
          label: "Copy coordinates",
          run: () =>
            void this.app.clipboard.copyText(`${Math.round(point.x)},${Math.round(point.z)}`),
        },
        {
          label: "Copy grid reference",
          run: () => void this.app.clipboard.copyText(grid ?? "Outside grid"),
        },
        { label: "Center here", run: () => this.app.fly.to(point) },
        {
          label: "Zoom in here",
          run: () =>
            this.app.fly.to({
              ...point,
              zoom: Math.min(
                this.app.session.extendedZoom ? 1024 : 400,
                (this.app.session.viewer?.state.zoom ?? 1) * 2,
              ),
            }),
        },
      ],
      this.app.canvas,
    );
  }
}
