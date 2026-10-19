import type { AppContext } from "./app-context";
import {
  selectTool,
  addMeasurePoint,
  finishMeasure,
  removeMeasurePoint,
  hitPin,
  type Point,
  type Tool,
} from "./tools";

export class MeasureController {
  constructor(private app: AppContext) {}
  setTool(tool: Tool) {
    this.app.state = selectTool(this.app.state, tool);
    this.app.canvas.dataset.tool = tool;
    document
      .querySelectorAll<HTMLButtonElement>("[data-tool]")
      .forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.tool === tool)));
    if (tool === "measure") this.app.drawerController.openDrawer("measure");
    else if (this.app.drawer === "measure") this.app.drawerController.closeDrawer();
    this.app.view.syncOverlay();
  }

  pushMeasureUndo() {
    this.app.undo.push({
      kind: "measure",
      before: [...this.app.state.points],
      finished: this.app.state.finished,
    });
  }

  finishMeasurement() {
    const next = finishMeasure(this.app.state);
    if (next === this.app.state || this.app.state.finished) return;
    this.pushMeasureUndo();
    this.app.state = next;
    this.app.view.syncOverlay();
    if (this.app.drawer === "measure") this.app.drawerController.renderDrawer();
  }

  undoMeasurePoint() {
    if (!this.app.state.points.length) return;
    this.pushMeasureUndo();
    this.app.state = removeMeasurePoint(this.app.state);
    this.app.view.syncOverlay();
    if (this.app.drawer) this.app.drawerController.renderDrawer();
  }

  addPoint(point: Point, alt = false) {
    if (!this.app.pointer.inBounds(point)) return;
    let p = point;
    if (!alt) {
      const viewer = this.app.session.viewer!;
      const screen = viewer.worldToScreen(point.x, point.z);
      const pin = hitPin(
        this.app.pins,
        (q) => {
          const xy = viewer.worldToScreen(q.x, q.z);
          return { x: xy.x, z: xy.y };
        },
        screen.x,
        screen.y,
        10,
      );
      if (pin) p = pin;
    }
    const viewer = this.app.session.viewer!;
    const next = addMeasurePoint(this.app.state, { x: p.x, z: p.z }, (q) => {
      const xy = viewer.worldToScreen(q.x, q.z);
      return { x: xy.x, z: xy.y };
    });
    if (next === this.app.state) return;
    this.pushMeasureUndo();
    this.app.state = next;
    this.app.view.syncOverlay();
    this.app.drawerController.openDrawer("measure");
  }

  bind() {
    document
      .querySelectorAll<HTMLButtonElement>("[data-tool]")
      .forEach((button) => (button.onclick = () => this.setTool(button.dataset.tool as Tool)));
  }
}
