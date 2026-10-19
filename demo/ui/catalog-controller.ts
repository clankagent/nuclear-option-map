import { $ } from "./dom";
import type { AppContext } from "./app-context";

export class CatalogController {
  private mapMenuIndex = 0;
  constructor(private app: AppContext) {}
  renderMaps() {
    const menu = $("map-menu");
    menu.replaceChildren();
    this.app.session.maps.forEach((map, i) => {
      const button = document.createElement("button");
      button.type = "button";
      button.role = "option";
      button.setAttribute("aria-selected", String(map.id === this.app.session.current?.id));
      button.textContent = map.name;
      button.onclick = () => {
        this.closeMapMenu();
        $("map-switcher").focus({ preventScroll: true });
        void this.switchMap(map.id);
      };
      button.onmouseenter = () => {
        this.mapMenuIndex = i;
      };
      menu.append(button);
    });
  }

  renderViewCards() {
    const cards = $("view-cards");
    cards.replaceChildren();
    this.app.session.viewOptions.forEach((variant, i) => {
      const loading = this.app.session.loadingVariantId === variant.id;
      const button = document.createElement("button");
      button.className = "view-card" + (loading ? " loading" : "");
      button.type = "button";
      button.setAttribute("aria-pressed", String(this.app.session.variantId === variant.id));
      button.setAttribute("aria-label", `${variant.label} view`);
      button.setAttribute("aria-busy", String(loading));
      const thumb = document.createElement("span");
      thumb.className = "thumb";
      thumb.style.backgroundImage = `url("${variant.image.replace(/"/g, "%22")}")`;
      const caption = document.createElement("span");
      caption.className = "card-caption";
      caption.textContent = variant.label;
      const status = document.createElement("small");
      if (loading) status.textContent = "Loading…";
      else if (this.app.session.variantId === variant.id) status.textContent = "Active";
      else {
        const key = document.createElement("kbd");
        key.textContent = String(i + 1);
        status.append(key);
      }
      caption.append(status);
      button.append(thumb, caption);
      button.onclick = () => void this.switchView(variant.id);
      cards.append(button);
    });
  }

  async switchMap(id: string) {
    if (id === this.app.session.current?.id) return;
    this.app.fly.cancel();
    await this.app.session.selectMap(id);
  }

  async switchView(id: string) {
    if (!this.app.session.current || id === this.app.session.variantId || this.app.session.loading)
      return;
    this.app.fly.cancel();
    await this.app.session.showMap(this.app.session.current, id);
  }

  toggleMapMenu(force = false) {
    const menu = $("map-menu");
    menu.hidden = force ? false : !menu.hidden;
    $("map-switcher").setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) {
      this.mapMenuIndex = Math.max(
        0,
        this.app.session.maps.findIndex((m) => m.id === this.app.session.current?.id),
      );
      menu.querySelectorAll("button")[this.mapMenuIndex]?.focus();
    }
  }

  closeMapMenu() {
    $("map-menu").hidden = true;
    $("map-switcher").setAttribute("aria-expanded", "false");
  }

  bind() {
    $("map-switcher").onclick = () => this.toggleMapMenu();
    $("map-switcher").onkeydown = (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        this.toggleMapMenu(true);
      }
    };
    $("map-menu").onkeydown = (event) => {
      const options = [...$("map-menu").querySelectorAll<HTMLButtonElement>("button")];
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        this.closeMapMenu();
        $("map-switcher").focus();
      }
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        this.mapMenuIndex =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? options.length - 1
              : (this.mapMenuIndex + (event.key === "ArrowDown" ? 1 : -1) + options.length) %
                options.length;
        options[this.mapMenuIndex]?.focus();
      }
    };
  }
}
