import type { PanelContext } from "./panel-context";
import type { Settings } from "./store";
import { el, section, button, row, field, select } from "./panel-dom";
export function renderSettingsPanel(body: HTMLElement, c: PanelContext) {
  const display = section(body, "Display");
  const checkbox = (parent: HTMLElement, label: string, key: keyof Settings, run: () => void) => {
    const node = el<HTMLInputElement>("input");
    node.type = "checkbox";
    node.checked = Boolean(c.settings[key]);
    node.onchange = () => {
      (c.settings as unknown as Record<string, unknown>)[key] = node.checked;
      run();
    };
    field(parent, label, node);
  };
  checkbox(display, "Grid", "grid", c.onSettings);
  field(
    display,
    "Labels",
    select(
      [
        ["edges", "Edges"],
        ["cells", "Cells"],
        ["none", "None"],
      ],
      c.settings.labels,
      (value) => {
        c.settings.labels = value as Settings["labels"];
        c.onSettings();
      },
    ),
  );
  if (c.session.variantId === "original") {
    const tint = el<HTMLInputElement>("input");
    tint.type = "color";
    tint.value = c.settings.tint;
    tint.oninput = () => {
      c.settings.tint = tint.value;
      c.onSettings();
    };
    field(display, "Builtin tint", tint);
  }
  if (c.session.viewer?.state.map?.contours) {
    const topo = section(body, "Topographic");
    const available = c.session.viewer.state.map.contours.intervals;
    field(
      topo,
      "Contour interval",
      select(
        [["auto", "Auto"], ...available.map((n) => [String(n), `${n} m`] as [string, string])],
        String(c.settings.contourInterval),
        (value) => {
          c.settings.contourInterval =
            value === "auto" ? "auto" : (Number(value) as Settings["contourInterval"]);
          c.onSettings();
        },
      ),
    );
    const smooth = el<HTMLInputElement>("input");
    smooth.type = "range";
    smooth.min = "0";
    smooth.max = "80";
    smooth.step = "4";
    smooth.value = String(c.settings.contourSmoothingMeters);
    smooth.oninput = () => {
      c.settings.contourSmoothingMeters = Number(smooth.value);
      c.onSettings();
    };
    field(topo, "Smoothing", smooth);
    if (c.session.viewer.state.map.topography) {
      field(
        topo,
        "Terrain fill",
        select(
          [
            ["plain", "Plain"],
            ["lowlands-dark", "Darker lowlands"],
            ["elevation-colors", "Elevation colors"],
          ],
          c.settings.topoStyle,
          (value) => {
            c.settings.topoStyle = value as Settings["topoStyle"];
            c.onSettings();
          },
        ),
      );
      checkbox(topo, "Shaded relief", "topoHillshade", c.onSettings);
      const strength = el<HTMLInputElement>("input");
      strength.type = "range";
      strength.min = "0";
      strength.max = "100";
      strength.step = "5";
      strength.value = String(Math.round(c.settings.topoStrength * 100));
      strength.oninput = () => {
        c.settings.topoStrength = Number(strength.value) / 100;
        c.onSettings();
      };
      field(topo, "Strength", strength);
    }
  }
  const navigation = section(body, "Navigation");
  const zoom = el<HTMLInputElement>("input");
  zoom.type = "checkbox";
  zoom.checked = c.session.extendedZoom;
  zoom.onchange = () => {
    c.session.extendedZoom = zoom.checked;
    c.session.viewer?.setMaxZoom(zoom.checked ? 1024 : 400);
  };
  field(navigation, "Extended zoom", zoom);
  navigation.append(
    el("p", "Allows up to 1,024× zoom instead of 400×. Higher zoom enlarges existing imagery."),
  );
  checkbox(navigation, "Show minimap", "minimap", c.onSettings);
  const units = section(body, "Units");
  field(
    units,
    "Range",
    select(
      [
        ["metric", "Metric (m / km)"],
        ["nautical", "Nautical (nm)"],
      ],
      c.settings.units,
      (value) => {
        c.settings.units = value as Settings["units"];
        c.onSettings();
      },
    ),
  );
  const info = section(body, "Other");
  const links = row(info);
  button(links, "Copy view link", c.onCopyLink);
  button(links, "Keyboard shortcuts", c.onShortcuts);
  button(links, "Reset defaults", c.onReset);
  info.append(
    el(
      "p",
      "Drag to pan; wheel or pinch to zoom. Builtin uses the map sprite with a tint. " +
        "Topographic contours follow extracted ground elevation. " +
        "Coordinates are meters from the map center: X east, Z north. Grid cells are 10 km wide.",
    ),
  );
}
