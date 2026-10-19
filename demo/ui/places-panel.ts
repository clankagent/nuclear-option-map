import type { PanelContext } from "./panel-context";
import { el, section, button, row } from "./panel-dom";
import { PIN_HEX } from "./overlay";
import { gridReference } from "../../src/catalog";
import { icon } from "./icons";

export function renderPlacesPanel(body: HTMLElement, c: PanelContext) {
  const s = section(body, `${c.pins.length} saved ${c.pins.length === 1 ? "pin" : "pins"}`);
  if (!c.pins.length) s.append(el("p", "Use the Pin tool to add a place on this map."));
  c.pins.forEach((pin) => {
    const item = el("div", undefined, "place-item");
    const fly = button(item, "", () => c.onFly(pin), "pin-row");
    const dot = el("span", undefined, "color-dot");
    dot.style.background = PIN_HEX[pin.color];
    const name = el("span", pin.name, "place-name");
    fly.append(
      dot,
      name,
      el(
        "span",
        c.session.current ? (gridReference(c.session.current, pin.x, pin.z) ?? "—") : "—",
        "mono",
      ),
    );
    const remove = button(item, "", () => c.onDelete(pin.id), "icon-button danger");
    remove.innerHTML = icon("close");
    remove.setAttribute("aria-label", `Delete ${pin.name}`);
    s.append(item);
  });
  const actions = row(s);
  button(actions, "Export JSON", c.onExport);
  const file = el<HTMLInputElement>("input");
  file.type = "file";
  file.accept = "application/json,.json";
  file.hidden = true;
  file.onchange = () => {
    if (file.files?.[0]) c.onImport(file.files[0]);
  };
  actions.append(file);
  button(actions, "Import JSON", () => file.click());
}
