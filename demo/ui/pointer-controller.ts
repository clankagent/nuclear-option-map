import type { AppContext } from "./app-context";
import { hitPin, hitPoint, type Point } from "./tools";
import type { Pin } from "./store";

export class PointerController {
  private pointerDown: {
    x: number;
    y: number;
    hit: Pin | null;
    measureIndex: number;
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
    const pos = this.local(event),
      viewer = this.app.session.viewer;
    const screen = (point: Point) => {
      const p = viewer.worldToScreen(point.x, point.z);
      return { x: p.x, z: p.y };
    };
    const hit = hitPin(this.app.pins, screen, pos.x, pos.y);
    const measureIndex =
      this.app.state.finished && this.app.state.tool === "measure"
        ? hitPoint(this.app.state.points, screen, pos.x, pos.y)
        : -1;
    this.pointerDown = { ...pos, hit, measureIndex, dragged: false };
    if (this.app.state.tool !== "navigate" || hit) {
      event.stopImmediatePropagation();
      event.preventDefault();
      this.app.canvas.setPointerCapture(event.pointerId);
      this.app.canvas.focus({ preventScroll: true });
    }
  };

  onPointerMove = (event: PointerEvent) => {
    if (!this.pointerDown) return;
    const pos = this.local(event),
      delta = Math.hypot(pos.x - this.pointerDown.x, pos.y - this.pointerDown.y);
    if (delta > 5 && !this.pointerDown.dragged) {
      if (this.pointerDown.measureIndex >= 0) this.app.measure.pushMeasureUndo();
      this.pointerDown.dragged = true;
    }
    if (!this.pointerDown.dragged || !this.app.session.viewer) return;
    if (this.pointerDown.hit && this.app.state.tool === "navigate") {
      const point = this.world(event);
      if (this.inBounds(point)) {
        this.app.pins = this.app.pins.map((p) =>
          p.id === this.pointerDown!.hit!.id ? { ...p, ...point } : p,
        );
        this.app.view.syncOverlay();
      }
      event.stopImmediatePropagation();
    }
    if (this.pointerDown.measureIndex >= 0) {
      const point = this.world(event);
      if (this.inBounds(point)) {
        this.app.state = {
          ...this.app.state,
          points: this.app.state.points.map((p, i) =>
            i === this.pointerDown!.measureIndex ? point : p,
          ),
        };
        this.app.view.syncOverlay();
      }
      event.stopImmediatePropagation();
    }
    if (this.app.state.tool === "measure" || this.app.state.tool === "pin")
      event.stopImmediatePropagation();
  };

  onPointerUp = (event: PointerEvent) => {
    const down = this.pointerDown;
    this.pointerDown = null;
    if (!down || !this.app.session.viewer) return;
    this.lastGestureDragged = down.dragged;
    if (this.app.state.tool !== "navigate" || down.hit) event.stopImmediatePropagation();
    if (down.dragged) {
      if (down.hit && this.app.state.tool === "navigate") {
        this.app.undo.push({
          kind: "pins",
          before: this.app.pins.map((p) => (p.id === down.hit!.id ? down.hit! : p)),
        });
        this.app.pinsController.saveCurrentPins();
      }
      if (down.measureIndex >= 0 && this.app.drawer === "measure")
        this.app.drawerController.renderDrawer();
      return;
    }
    if (this.app.state.tool === "pin")
      this.app.pinsController.dropPin(this.world(event), event.shiftKey);
    else if (this.app.state.tool === "measure") {
      if (down.measureIndex < 0 && event.detail < 2)
        this.app.measure.addPoint(this.world(event), event.altKey);
    } else if (down.hit) {
      this.app.state = { ...this.app.state, selectedPin: down.hit.id };
      this.app.view.syncOverlay();
      this.app.drawerController.openDrawer("pin");
    } else {
      this.app.state = { ...this.app.state, selectedPin: null };
      if (this.app.drawer === "pin") this.app.drawerController.closeDrawer();
      this.app.view.syncOverlay();
    }
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
      if (this.app.state.tool === "measure") {
        event.preventDefault();
        this.app.measure.finishMeasurement();
        return;
      }
      if (this.app.state.tool !== "navigate") return;
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
