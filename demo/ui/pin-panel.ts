import type { PanelContext } from "./panel-context";
import { el, section, button, row, field } from "./panel-dom";
import { PIN_COLORS, type PinColor } from "./store";
import { PIN_HEX } from "./overlay";
import { gridReference } from "../../src/catalog";
import { icon } from "./icons";
import { formatCoordinate } from "./geo";

export function renderPinPanel(body: HTMLElement, c: PanelContext) {
  const pin = c.pins.find((item) => item.id === c.state.selectedPin);
  if (!pin) return;
  const s = el<HTMLElement>("section", undefined, "pin-form");
  body.append(s);
  const name = el<HTMLInputElement>("input");
  name.type = "text";
  name.maxLength = 40;
  name.value = pin.name;
  name.setAttribute("aria-label", "Pin name");
  name.onchange = () => {
    const trimmed = name.value.trim();
    if (trimmed) c.onPin({ ...pin, name: trimmed });
    else name.value = pin.name;
  };
  field(s, "Name", name);
  const colors = row(s);
  PIN_COLORS.forEach((color) => {
    const swatch = button(colors, "", () => c.onPin({ ...pin, color }), "swatch");
    swatch.style.background = PIN_HEX[color as PinColor];
    swatch.setAttribute("aria-label", `${color} pin`);
    swatch.setAttribute("aria-pressed", String(pin.color === color));
  });
  const grid = c.session.current ? gridReference(c.session.current, pin.x, pin.z) : null;
  const coordinates = el("div", undefined, "pin-coordinates");
  coordinates.append(
    el(
      "span",
      `${grid ? grid + " · " : ""}x ${formatCoordinate(pin.x)} · z ${formatCoordinate(pin.z)}`,
      "mono",
    ),
  );
  const copy = button(
    coordinates,
    "",
    () => c.onCopy(`${Math.round(pin.x)},${Math.round(pin.z)}`),
    "icon-button",
  );
  copy.innerHTML = icon("copy");
  copy.setAttribute("aria-label", "Copy pin coordinates");
  s.append(coordinates);
  const actions = row(s);
  button(actions, "Measure from here", () => c.onMeasureFrom(pin));
  button(actions, "Delete pin", () => c.onDelete(), "danger");
}
