import type { Pin } from "./store";
export type Point = { x: number; z: number };
export type Tool = "navigate" | "measure" | "pin";
export type ToolState = {
  tool: Tool;
  points: Point[];
  finished: boolean;
  selectedPin: string | null;
};
export type UndoAction =
  | { kind: "pins"; before: Pin[] }
  | { kind: "measure"; before: Point[]; finished: boolean };
export function initialToolState(): ToolState {
  return { tool: "navigate", points: [], finished: false, selectedPin: null };
}
export function selectTool(state: ToolState, tool: Tool): ToolState {
  return { ...state, tool, selectedPin: tool === "navigate" ? state.selectedPin : null };
}
export function addMeasurePoint(
  state: ToolState,
  point: Point,
  screen?: (point: Point) => Point,
): ToolState {
  const previous = !state.finished ? state.points.at(-1) : undefined;
  if (previous) {
    if (previous.x === point.x && previous.z === point.z) return state;
    if (screen) {
      const a = screen(previous),
        b = screen(point);
      if (Math.hypot(a.x - b.x, a.z - b.z) < 4) return state;
    }
  }
  return {
    ...state,
    tool: "measure",
    points: state.finished ? [point] : [...state.points, point],
    finished: false,
  };
}
export function finishMeasure(state: ToolState): ToolState {
  return state.points.length >= 2 ? { ...state, finished: true } : state;
}
export function removeMeasurePoint(state: ToolState): ToolState {
  return { ...state, points: state.points.slice(0, -1), finished: false };
}
export function hitPin(
  pins: Pin[],
  screen: (p: Point) => Point,
  x: number,
  y: number,
  radius = 12,
): Pin | null {
  for (let i = pins.length - 1; i >= 0; i--) {
    const p = screen(pins[i]);
    if (Math.hypot(p.x - x, p.z - y) <= radius) return pins[i];
  }
  return null;
}
export function hitPoint(
  points: Point[],
  screen: (p: Point) => Point,
  x: number,
  y: number,
  radius = 12,
): number {
  for (let i = points.length - 1; i >= 0; i--) {
    const p = screen(points[i]);
    if (Math.hypot(p.x - x, p.z - y) <= radius) return i;
  }
  return -1;
}
export class UndoStack {
  private stack: UndoAction[] = [];
  push(action: UndoAction) {
    this.stack.push(action);
    if (this.stack.length > 20) this.stack.shift();
  }
  pop() {
    return this.stack.pop();
  }
  clear() {
    this.stack = [];
  }
  get length() {
    return this.stack.length;
  }
}
