import type { AppContext } from "./app-context";
import type { Point } from "./geo";

export class PointerController {
  private pointerDown: {
    x: number;
    y: number;
    dragged: boolean;
  } | null = null;
  private lastGestureDragged = false;
  constructor(private app: AppContext) {}
  local(event: PointerEvent | MouseEvent) {
    const r = this.app.canvas.getBoundingClientRect();
    return { x: event.clientX - r.left, y: event.clientY - r.top };
  }

  world(event: PointerEvent | MouseEvent): Point {
    const p = this.local(event);
    return this.app.session.viewer?.screenToWorld(p.x, p.y) ?? { x: 0, z: 0 };
  }

  inBounds(p: Point) {
    const map = this.app.session.current;
    return !!map && Math.abs(p.x) <= map.widthMeters / 2 && Math.abs(p.z) <= map.heightMeters / 2;
  }

  onPointerDown = (event: PointerEvent) => {
    this.app.fly.cancel();
    if (event.button !== 0 || !this.app.session.viewer || !this.app.session.current) return;
    this.pointerDown = { ...this.local(event), dragged: false };
  };

  onPointerMove = (event: PointerEvent) => {
    if (!this.pointerDown) return;
    const pos = this.local(event),
      delta = Math.hypot(pos.x - this.pointerDown.x, pos.y - this.pointerDown.y);
    if (delta > 5 && !this.pointerDown.dragged) {
      this.pointerDown.dragged = true;
    }
  };

  onPointerUp = (event: PointerEvent) => {
    const down = this.pointerDown;
    this.pointerDown = null;
    if (!down || !this.app.session.viewer) return;
    this.lastGestureDragged = down.dragged;
  };

  bind() {
    this.app.canvas.addEventListener("pointerdown", this.onPointerDown, { capture: true });
    this.app.canvas.addEventListener("pointermove", this.onPointerMove, { capture: true });
    this.app.canvas.addEventListener("pointerup", this.onPointerUp, { capture: true });
    this.app.canvas.addEventListener(
      "pointercancel",
      () => {
        this.pointerDown = null;
      },
      { capture: true },
    );
    this.app.canvas.addEventListener("wheel", this.app.fly.cancel, {
      capture: true,
      passive: true,
    });
    this.app.canvas.addEventListener("dblclick", (event) => {
      if (this.lastGestureDragged) return;
      const point = this.world(event);
      if (this.inBounds(point))
        this.app.session.viewer?.setView({
          centerX: point.x,
          centerZ: point.z,
          zoom: Math.min(
            this.app.session.extendedZoom ? 1024 : 400,
            this.app.session.viewer.state.zoom * 2,
          ),
        });
    });
    this.app.canvas.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      this.app.context.openContext(event.clientX, event.clientY, this.world(event));
    });
    let longPress = 0,
      start: Point | null = null;
    this.app.canvas.addEventListener(
      "touchstart",
      (event) => {
        clearTimeout(longPress);
        if (event.touches.length !== 1) {
          start = null;
          return;
        }
        const t = event.touches[0];
        start = { x: t.clientX, z: t.clientY };
        longPress = window.setTimeout(() => {
          this.pointerDown = null;
          this.app.context.openContext(
            t.clientX,
            t.clientY,
            this.world(t as unknown as MouseEvent),
          );
          longPress = 0;
        }, 500);
      },
      { passive: true },
    );
    this.app.canvas.addEventListener(
      "touchmove",
      (event) => {
        const touch = event.touches[0];
        if (start && touch && Math.hypot(touch.clientX - start.x, touch.clientY - start.z) > 8)
          clearTimeout(longPress);
      },
      { passive: true },
    );
    for (const name of ["touchend", "touchcancel"])
      this.app.canvas.addEventListener(name, () => {
        clearTimeout(longPress);
        start = null;
      });
  }
}
