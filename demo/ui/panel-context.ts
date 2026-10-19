import type { MapSession } from "../map-session";
import type { Settings, Pin } from "./store";
import type { Point, ToolState } from "./tools";

export type PanelKind = "settings" | "places" | "layers" | "pin" | "measure";
export type PanelContext = {
  session: MapSession;
  settings: Settings;
  state: ToolState;
  pins: Pin[];
  onSettings: () => void;
  onPin: (pin: Pin) => void;
  onDelete: (id?: string) => void;
  onMeasureFrom: (point: Point) => void;
  onFly: (point: Point) => void;
  onExport: () => void;
  onImport: (file: File) => void;
  onCopy: (text: string) => void;
  onUndoPoint: () => void;
  onClearMeasure: () => void;
  onFinishMeasure: () => void;
  onView: (id: string) => void;
  onShortcuts: () => void;
  onCopyLink: () => void;
  onReset: () => void;
};
