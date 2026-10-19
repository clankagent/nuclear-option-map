import type { PanelContext } from "./panel-context";
import { section, button, row } from "./panel-dom";

export function renderLayersPanel(body: HTMLElement, c: PanelContext) {
  const s = section(body, "Views");
  c.session.viewOptions.forEach((variant) => {
    const b = button(s, variant.label, () => c.onView(variant.id), "pin-row");
    b.setAttribute("aria-pressed", String(c.session.variantId === variant.id));
    if (c.session.loading) b.disabled = true;
  });
}
