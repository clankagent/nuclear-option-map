import type { AppContext } from "./app-context";

export class HistoryController {
  constructor(private app: AppContext) {}
  undoLast() {
    const action = this.app.undo.pop();
    if (!action) return;
    if (action.kind === "pins") {
      this.app.pins = action.before;
      if (!this.app.pins.some((p) => p.id === this.app.state.selectedPin)) {
        this.app.state = { ...this.app.state, selectedPin: null };
        if (this.app.drawer === "pin") this.app.drawerController.closeDrawer();
      }
      this.app.pinsController.saveCurrentPins();
    } else {
      this.app.state = { ...this.app.state, points: action.before, finished: action.finished };
      this.app.view.syncOverlay();
      if (this.app.drawer) this.app.drawerController.renderDrawer();
    }
  }
}
