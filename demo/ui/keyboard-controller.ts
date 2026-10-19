import { $ } from "./dom";
import type { AppContext } from "./app-context";

export class KeyboardController {
  constructor(private app: AppContext) {}
  onKey(event: KeyboardEvent) {
    const dialog = $<HTMLDialogElement>("shortcuts-dialog");
    if (dialog.open) return;
    const target = event.target instanceof HTMLElement ? event.target : this.app.canvas;
    const editing = target.matches("input,textarea,select,[contenteditable=true]");
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      $<HTMLInputElement>("jump-input").focus();
      document.querySelector(".jump-wrap")?.setAttribute("data-open", "true");
      return;
    }
    if (event.defaultPrevented) return;
    if (event.key === "Escape" && this.app.drawer && editing) {
      event.preventDefault();
      this.app.drawerController.closeDrawer();
      return;
    }
    if (editing) return;
    if (event.key === "Escape") {
      if (this.app.menu.isOpen) this.app.menu.close();
      else if (!$("jump-results").hidden) this.app.search.closeSearch();
      else if (!$("map-menu").hidden) this.app.catalog.closeMapMenu();
      else if (this.app.drawer) this.app.drawerController.closeDrawer();
      return;
    }
    if ((event.key === "F10" && event.shiftKey) || event.key === "ContextMenu") {
      event.preventDefault();
      const box = this.app.canvas.getBoundingClientRect(),
        s = this.app.session.viewer?.state;
      if (s)
        this.app.context.openContext(box.left + box.width / 2, box.top + box.height / 2, {
          x: s.centerX,
          z: s.centerZ,
        });
      return;
    }
    const key = event.key.toLowerCase();
    const shortcuts: Record<string, () => void> = {
      "/": () => {
        $<HTMLInputElement>("jump-input").focus();
        document.querySelector(".jump-wrap")?.setAttribute("data-open", "true");
      },
      b: () => this.app.drawerController.openDrawer("places"),
      l: () => this.app.drawerController.openDrawer("layers"),
      g: () => {
        this.app.settings.grid = !this.app.settings.grid;
        this.app.settingsController.applySettings();
      },
      h: () => {
        this.app.settings.minimap = !this.app.settings.minimap;
        this.app.settingsController.applySettings();
      },
      ",": () => this.app.drawerController.openDrawer("settings"),
      "?": () => this.app.showShortcuts(),
    };
    if (["1", "2", "3"].includes(key) && !event.altKey && !event.ctrlKey && !event.metaKey) {
      const variant = this.app.session.viewOptions[Number(key) - 1];
      if (variant) void this.app.catalog.switchView(variant.id);
    } else if (shortcuts[key] && !event.altKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      shortcuts[key]();
    }
  }

  bind() {
    document.addEventListener("pointerdown", (event) => {
      this.app.fly.cancel();
      if (
        !$("map-switcher").contains(event.target as Node) &&
        !$("map-menu").contains(event.target as Node)
      )
        this.app.catalog.closeMapMenu();
      if (
        !$("jump-panel").contains(event.target as Node) &&
        !$("search-open").contains(event.target as Node)
      )
        this.app.search.closeSearch();
      if (this.app.menu.isOpen && !$("context-menu").contains(event.target as Node))
        this.app.menu.close();
    });
    document.addEventListener("keydown", this.app.fly.cancel, { capture: true });
    document.addEventListener("keydown", (event) => this.onKey(event));
    window.addEventListener("hashchange", () => void this.app.permalink.restoreHash());
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) void this.app.chrome.refreshStatus();
    });
  }
}
