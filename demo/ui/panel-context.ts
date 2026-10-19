import type { MapSession } from "../map-session";
import type { Settings, Pin } from "./store";
import type { Point } from "./geo";

export type PanelKind = "settings" | "places" | "layers";
export type PanelContext = {
  session: MapSession;
  settings: Settings;
  pins: Pin[];
  onSettings: () => void;
  onDelete: (id?: string) => void;
  onFly: (point: Point) => void;
  onExport: () => void;
  onImport: (file: File) => void;
  onView: (id: string) => void;
  onShortcuts: () => void;
  onCopyLink: () => void;
  onReset: () => void;
};
