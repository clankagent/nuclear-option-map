import type { PanelContext, PanelKind } from "./panel-context";
import { renderLayersPanel } from "./layers-panel";
import { renderPlacesPanel } from "./places-panel";
import { renderSettingsPanel } from "./settings-panel";

export type { PanelContext, PanelKind } from "./panel-context";

const renderers = {
  layers: renderLayersPanel,
  places: renderPlacesPanel,
  settings: renderSettingsPanel,
};

export function renderPanel(kind: PanelKind, body: HTMLElement, context: PanelContext) {
  body.replaceChildren();
  renderers[kind](body, context);
}
