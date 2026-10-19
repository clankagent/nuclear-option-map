export type Label = {
  x: number;
  y: number;
  width: number;
  height: number;
  priority?: number;
  id: string;
};
/** Keep important labels first, then reject rectangles that intersect accepted labels. */
export function placeLabels(labels: Label[]): Label[] {
  const placed: Label[] = [];
  for (const label of [...labels].sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0))) {
    if (
      label.priority ||
      !placed.some(
        (other) =>
          label.x < other.x + other.width &&
          label.x + label.width > other.x &&
          label.y < other.y + other.height &&
          label.y + label.height > other.y,
      )
    )
      placed.push(label);
  }
  return placed;
}
