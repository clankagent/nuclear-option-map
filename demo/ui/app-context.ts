import type { MapPointer } from "../../src/viewer";
import { MapSession } from "../map-session";
import { loadSettings, type Pin } from "./store";
import { initialToolState, UndoStack, type ToolState } from "./tools";
import { Overlay } from "./overlay";
import { Minimap } from "./minimap";
import { FlyController } from "./fly";
import { ContextMenu } from "./context-menu";
import { Toasts } from "./toast";
import { setupShortcuts } from "./shortcuts";
import type { PanelKind } from "./panels";
import { $ } from "./dom";
import { ViewController } from "./view-controller";
import { CatalogController } from "./catalog-controller";
import { SearchController } from "./search-controller";
import { DrawerController } from "./drawer-controller";
import { SettingsController } from "./settings-controller";
import { PinsController } from "./pins-controller";
import { MeasureController } from "./measure-controller";
import { HistoryController } from "./history-controller";
import { PermalinkController } from "./permalink-controller";
import { ClipboardController } from "./clipboard-controller";
import { PointerController } from "./pointer-controller";
import { ContextController } from "./context-controller";
import { KeyboardController } from "./keyboard-controller";
import { ChromeController } from "./chrome-controller";

/** Shared session state and controller wiring. Each controller owns its transient UI state. */
export class AppContext {
  readonly root = document.querySelector<HTMLElement>(".map-app")!;
  readonly canvas = $<HTMLCanvasElement>("map-canvas");
  readonly settings = loadSettings();
  readonly session = new MapSession(this.canvas, this.settings);
  state: ToolState = initialToolState();
  pins: Pin[] = [];
  readonly overlay = new Overlay($<HTMLCanvasElement>("map-overlay"), () => this.session.viewer);
  readonly minimap = new Minimap(
    $("minimap"),
    $("minimap-surface"),
    $("minimap-viewport"),
    () => this.session.viewer,
  );
  readonly fly = new FlyController(() => this.session.viewer);
  readonly menu = new ContextMenu($("context-menu"));
  readonly toasts = new Toasts($("toasts"));
  readonly undo = new UndoStack();
  drawer: PanelKind | null = null;
  readoutPoint: MapPointer | null = null;
  readonly showShortcuts = setupShortcuts(
    $<HTMLDialogElement>("shortcuts-dialog"),
    $("shortcuts-body"),
    $<HTMLButtonElement>("shortcuts-close"),
  );
  readonly view = new ViewController(this);
  readonly catalog = new CatalogController(this);
  readonly search = new SearchController(this);
  readonly drawerController = new DrawerController(this);
  readonly settingsController = new SettingsController(this);
  readonly pinsController = new PinsController(this);
  readonly measure = new MeasureController(this);
  readonly history = new HistoryController(this);
  readonly permalink = new PermalinkController(this);
  readonly clipboard = new ClipboardController(this);
  readonly pointer = new PointerController(this);
  readonly context = new ContextController(this);
  readonly keyboard = new KeyboardController(this);
  readonly chrome = new ChromeController(this);
}
