import { $ } from "./dom";
import type { AppContext } from "./app-context";
import { searchLocations, type SearchResult } from "./search";

export class SearchController {
  private searchResults: SearchResult[] = [];
  private searchIndex = 0;
  constructor(private app: AppContext) {}
  renderSearch() {
    const map = this.app.session.current,
      input = $<HTMLInputElement>("jump-input"),
      host = $("jump-results");
    if (!map) return;
    this.searchResults = searchLocations(input.value, map, this.app.pins);
    host.replaceChildren();
    host.hidden = !input.value.trim();
    input.setAttribute("aria-expanded", String(!host.hidden));
    if (!this.searchResults.length && !host.hidden) {
      const hint = document.createElement("div");
      hint.className = "jump-hint";
      hint.textContent =
        "Use a grid cell such as E4, coordinates such as 12000,-4500, or a saved pin name.";
      host.append(hint);
    }
    this.searchResults.forEach((result, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.role = "option";
      b.setAttribute("aria-selected", String(i === this.searchIndex));
      b.textContent = result.label;
      const small = document.createElement("small");
      small.textContent = result.detail;
      b.append(small);
      b.onpointerdown = (e) => e.preventDefault();
      b.onclick = () => this.chooseSearch(result);
      host.append(b);
    });
  }

  chooseSearch(result?: SearchResult) {
    if (!result) return;
    const viewer = this.app.session.viewer!;
    let zoom = viewer.state.zoom;
    if (result.location?.kind === "grid")
      zoom = Math.max(
        zoom,
        Math.min(
          400,
          (Math.min(viewer.state.width, viewer.state.height) / (10000 * viewer.state.scale)) *
            viewer.state.zoom *
            0.7,
        ),
      );
    this.app.fly.to({ x: result.x, z: result.z, zoom });
    const input = $<HTMLInputElement>("jump-input");
    input.value = "";
    input.blur();
    this.searchResults = [];
    this.searchIndex = 0;
    this.closeSearch();
    this.app.canvas.focus({ preventScroll: true });
  }

  closeSearch() {
    $("jump-results").hidden = true;
    $("jump-input").setAttribute("aria-expanded", "false");
    document.querySelector(".jump-wrap")?.removeAttribute("data-open");
  }

  bind() {
    $("search-open").onclick = () => {
      document.querySelector(".jump-wrap")?.setAttribute("data-open", "true");
      $<HTMLInputElement>("jump-input").focus();
    };
    const search = $<HTMLInputElement>("jump-input");
    search.oninput = () => {
      this.searchIndex = 0;
      this.renderSearch();
    };
    search.onkeydown = (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (this.searchResults.length)
          this.searchIndex =
            (this.searchIndex + (event.key === "ArrowDown" ? 1 : -1) + this.searchResults.length) %
            this.searchResults.length;
        this.renderSearch();
      } else if (event.key === "Enter") {
        event.preventDefault();
        this.chooseSearch(this.searchResults[this.searchIndex]);
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        if (search.value) {
          search.value = "";
          this.renderSearch();
        } else {
          this.closeSearch();
          search.blur();
          this.app.canvas.focus({ preventScroll: true });
        }
      }
    };
  }
}
