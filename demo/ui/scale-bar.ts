import { niceScaleBar, type UnitSystem } from "./geo";
export function updateScaleBar(
  scale: number,
  units: UnitSystem,
  line: HTMLElement,
  label: HTMLElement,
) {
  if (!Number.isFinite(scale) || scale <= 0) return;
  const result = niceScaleBar(1 / scale, units);
  line.style.width = `${result.pixels}px`;
  label.textContent = result.label;
}
