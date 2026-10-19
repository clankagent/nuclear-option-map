import type { PanelContext } from "./panel-context";
import { el, section, button, row, field } from "./panel-dom";
import {
  distanceMeters,
  formatBearing,
  bearingDegrees,
  formatCoordinate,
  formatDistance,
  formatDuration,
  travelTimeSeconds,
  MACH_1_KNOTS,
} from "./geo";
import { renderPanel } from "./panels";
export function renderMeasurePanel(body: HTMLElement, c: PanelContext) {
  const s = el<HTMLElement>("section");
  body.append(s);
  if (!c.state.points.length)
    s.append(el("p", "Click the map to add route points. Enter finishes the route."));
  const legs = c.state.points.slice(1).map((point, i) => ({
    distance: distanceMeters(c.state.points[i], point),
    bearing: formatBearing(bearingDegrees(c.state.points[i], point)),
  }));
  const total = legs.reduce((sum, leg) => sum + leg.distance, 0);
  const figure = el("div", undefined, "measure-total");
  figure.append(
    el("span", "Total distance", "eyebrow"),
    el("strong", formatDistance(total, c.settings.units), "mono"),
    el(
      "span",
      formatDistance(total, c.settings.units === "metric" ? "nautical" : "metric"),
      "mono",
    ),
  );
  s.append(figure);
  const table = el<HTMLTableElement>("table", undefined, "data-table");
  const head = table.createTHead().insertRow();
  ["Leg", "Distance", "Bearing"].forEach((title) => {
    const th = el<HTMLTableCellElement>("th", title);
    th.scope = "col";
    head.append(th);
  });
  const rows = table.createTBody();
  legs.forEach((leg, i) => {
    const r = rows.insertRow();
    [String(i + 1), formatDistance(leg.distance, c.settings.units), leg.bearing].forEach((value) =>
      r.append(el("td", value, "mono")),
    );
  });
  if (legs.length) s.append(table);
  if (c.state.points.length) {
    const details = el<HTMLDetailsElement>("details");
    details.append(el("summary", `${c.state.points.length} route points`));
    const list = el("ol", undefined, "point-list mono");
    c.state.points.forEach((point) =>
      list.append(el("li", `x ${formatCoordinate(point.x)} · z ${formatCoordinate(point.z)}`)),
    );
    details.append(list);
    s.append(details);
  }
  const actions = row(s);
  button(actions, "Undo last", c.onUndoPoint).disabled = !c.state.points.length;
  button(actions, "Finish", c.onFinishMeasure).disabled =
    c.state.points.length < 2 || c.state.finished;
  button(actions, "Clear", c.onClearMeasure).disabled = !c.state.points.length;
  const travel = section(body, "Travel time");
  const speed = el<HTMLInputElement>("input");
  speed.type = "number";
  speed.min = "50";
  speed.max = "2000";
  speed.step = "any";
  speed.value = String(c.settings.speedKnots);
  speed.onchange = () => {
    c.settings.speedKnots = Math.max(50, Math.min(2000, Number(speed.value) || 600));
    c.onSettings();
    renderPanel("measure", body, c);
  };
  field(travel, "Speed (kt)", speed);
  const times = el<HTMLTableElement>("table", undefined, "data-table travel-table");
  const timeHead = times.createTHead().insertRow();
  ["Speed", "Duration"].forEach((title) => {
    const th = el<HTMLTableCellElement>("th", title);
    th.scope = "col";
    timeHead.append(th);
  });
  const timeRows = times.createTBody(),
    speeds = [250, 450, 600, 900, MACH_1_KNOTS];
  if (!speeds.includes(c.settings.speedKnots)) speeds.push(c.settings.speedKnots);
  speeds.forEach((value) => {
    const r = timeRows.insertRow();
    r.classList.toggle("selected", value === c.settings.speedKnots);
    const cell = r.insertCell();
    const b = button(cell, value === MACH_1_KNOTS ? "Mach 1" : `${value} kt`, () => {
      c.settings.speedKnots = value;
      c.onSettings();
      renderPanel("measure", body, c);
    });
    b.setAttribute("aria-pressed", String(value === c.settings.speedKnots));
    r.append(el("td", formatDuration(travelTimeSeconds(total, value)), "mono"));
  });
  travel.append(times);
}
