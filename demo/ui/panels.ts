import type { PanelContext, PanelKind } from "./panel-context";
import { renderLayersPanel } from "./layers-panel";
import { renderPlacesPanel } from "./places-panel";
import { renderPinPanel } from "./pin-panel";
import { renderMeasurePanel } from "./measure-panel";
import { renderSettingsPanel } from "./settings-panel";

export type { PanelContext, PanelKind } from "./panel-context";

const renderers = {
  layers: renderLayersPanel,
  places: renderPlacesPanel,
  pin: renderPinPanel,
  measure: renderMeasurePanel,
  settings: renderSettingsPanel,
};

export function renderPanel(kind: PanelKind, body: HTMLElement, context: PanelContext) {
  body.replaceChildren();
  renderers[kind](body, context);
}
