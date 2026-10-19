import type { Point } from "./geo";

export type RulerSegment = { start: Point; end: Point };
export type Ruler = RulerSegment & { links: number[] };
export const RULER_JOIN_RADIUS_PX = 10;

/** Record endpoint contact when drawn. Zoom must not change an existing group's membership. */
export function joinRuler(segment: RulerSegment, existing: Ruler[], scale: number): Ruler {
  const radius = RULER_JOIN_RADIUS_PX / scale;
  const touches = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.z - b.z) <= radius;
  const links = existing.flatMap((ruler, index) =>
    [segment.start, segment.end].some((a) => [ruler.start, ruler.end].some((b) => touches(a, b)))
      ? [index]
      : [],
  );
  return { ...segment, links };
}

/** Sum each ruler once, including branches and closed loops. Removing a bridge can split a group. */
export function rulerGroups(rulers: Ruler[]): { indices: number[]; meters: number }[] {
  const parents = rulers.map((_, index) => index);
  const root = (index: number): number => {
    while (parents[index] !== index) {
      parents[index] = parents[parents[index]];
      index = parents[index];
    }
    return index;
  };
  rulers.forEach((ruler, index) => {
    for (const other of ruler.links) parents[root(index)] = root(other);
  });
  const groups = new Map<number, { indices: number[]; meters: number }>();
  rulers.forEach((ruler, index) => {
    const id = root(index),
      group = groups.get(id) ?? { indices: [], meters: 0 };
    group.indices.push(index);
    group.meters += Math.hypot(ruler.end.x - ruler.start.x, ruler.end.z - ruler.start.z);
    groups.set(id, group);
  });
  return [...groups.values()];
}
