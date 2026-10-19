import { $ } from "./dom";
import type { AppContext } from "./app-context";
import { icon } from "./icons";
import { statusChip } from "./status-chip";
import { saveSettings } from "./store";

export class ChromeController {
  private retry: (() => void) | null = null;
  private lastStatusFetch = 0;
  constructor(private app: AppContext) {}
  fillIcons() {
    const entries: [string, string][] = [
      ["chevron-icon", "chevron"],
      ["search-icon", "search"],
      ["search-open", "search"],
      ["settings-open", "settings"],
      ["places-open", "places"],
      ["layers-open", "layers"],
      ["copy-link", "link"],
      ["drawer-close", "close"],
    ];
    entries.forEach(([id, name]) => {
      $(id).innerHTML = icon(name);
    });
    document.querySelectorAll<HTMLButtonElement>("[data-tool]").forEach((button) => {
      button.innerHTML = icon(
        button.dataset.tool === "navigate"
          ? "hand"
          : button.dataset.tool === "measure"
            ? "measure"
            : "pin",
      );
    });
  }

  showLoading(title: string, detail: string, error: boolean) {
    const load = $("load-state"),
      banner = $("error-banner");
    if (!title) {
      load.hidden = true;
      banner.hidden = true;
      this.retry = null;
      return;
    }
    this.retry =
      this.app.session.current && this.app.session.retryLoad
        ? () => void this.app.session.retryLoad!()
        : () => void this.app.permalink.initializeSession();
    if (this.app.session.current && !error) {
      load.hidden = true;
      banner.hidden = true;
      return;
    }
    if (this.app.session.current && error) {
      load.hidden = true;
      banner.hidden = false;
      $("error-message").textContent = `${title}: ${detail}`;
      return;
    }
    banner.hidden = true;
    load.hidden = false;
    load.classList.toggle("error", error);
    $("load-title").textContent = title;
    $("load-detail").textContent = detail;
    $("load-retry").hidden = !error;
  }

  async refreshStatus() {
    if (document.hidden || Date.now() - this.lastStatusFetch < 60000) return;
    this.lastStatusFetch = Date.now();
    try {
      const response = await fetch("/maps/render-status/project-status.json", {
        cache: "no-store",
      });
      if (!response.ok) throw new Error("Unavailable");
      const summary = statusChip(await response.json());
      const chip = $("status-chip");
      chip.hidden = false;
      chip.dataset.tone = summary.tone;
      $("status-text").textContent = summary.text;
    } catch {
      $("status-chip").hidden = true;
    }
  }
  bind() {
    $("zoom-in").onclick = () => this.app.session.viewer?.zoomIn();
    $("zoom-out").onclick = () => this.app.session.viewer?.zoomOut();
    $("fit").onclick = () => this.app.session.viewer?.fit();
    $("minimap-collapse").onclick = () => {
      this.app.settings.minimapCollapsed = !this.app.settings.minimapCollapsed;
      saveSettings(this.app.settings);
      this.app.view.syncSession();
      $("minimap-collapse").setAttribute(
        "aria-expanded",
        String(!this.app.settings.minimapCollapsed),
      );
    };
    $("readout-copy").onclick = () => {
      const p = this.app.readoutPoint ?? this.app.session.viewer?.state;
      if (p)
        void this.app.clipboard.copyText(
          `${Math.round("x" in p ? p.x : p.centerX)},${Math.round("z" in p ? p.z : p.centerZ)}`,
        );
    };
    $("copy-link").onclick = () => void this.app.permalink.copyLink();
    $("error-retry").onclick = () => this.retry?.();
    $("load-retry").onclick = () => this.retry?.();
    $("error-close").onclick = () => {
      $("error-banner").hidden = true;
    };
  }
}
