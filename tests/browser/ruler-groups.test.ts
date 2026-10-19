import { describe, expect, it } from "vite-plus/test";
import { joinRuler, rulerGroups, type Ruler } from "../../demo/ui/ruler-groups";

const segment = (x: number, z: number, endX: number, endZ: number) => ({
  start: { x, z },
  end: { x: endX, z: endZ },
});

describe("temporary ruler groups", () => {
  it("joins nearby endpoints without changing individual lengths", () => {
    const first = joinRuler(segment(0, 0, 100, 0), [], 1);
    const next = segment(108, 0, 108, 60),
      second = joinRuler(next, [first], 1);
    expect(second.links).toEqual([0]);
    expect(second.start).toEqual(next.start);
    expect(rulerGroups([first, second])).toEqual([{ indices: [0, 1], meters: 160 }]);
    expect(joinRuler(segment(111, 0, 150, 0), [first], 1).links).toEqual([]);
    expect(joinRuler(segment(50, -50, 50, 50), [first], 1).links).toEqual([]);
  });
  it("uses screen-sized tolerance when drawing, then retains connections", () => {
    const first = joinRuler(segment(0, 0, 1000, 0), [], 0.1);
    const next = segment(1080, 0, 1200, 0);
    expect(joinRuler(next, [first], 0.1).links).toEqual([0]);
    expect(joinRuler(next, [first], 1).links).toEqual([]);
    const rulers = [first, joinRuler(next, [first], 0.1)];
    expect(rulerGroups(rulers)[0].meters).toBe(1120);
  });
  it("sums transitive, reversed, branching, and loop connections once", () => {
    const rulers: Ruler[] = [];
    for (const line of [
      segment(0, 0, 100, 0),
      segment(100, 100, 100, 0),
      segment(100, 100, 0, 0),
      segment(100, 100, 200, 100),
    ]) {
      rulers.push(joinRuler(line, rulers, 1));
    }
    const groups = rulerGroups(rulers);
    expect(groups).toHaveLength(1);
    expect(groups[0].indices).toEqual([0, 1, 2, 3]);
    expect(groups[0].meters).toBeCloseTo(300 + Math.hypot(100, 100));
  });
  it("splits groups again when undo removes their connecting ruler", () => {
    const first = joinRuler(segment(0, 0, 100, 0), [], 1);
    const second = joinRuler(segment(200, 0, 300, 0), [first], 1);
    const bridge = joinRuler(segment(100, 0, 200, 0), [first, second], 1);
    expect(rulerGroups([first, second, bridge])).toEqual([{ indices: [0, 1, 2], meters: 300 }]);
    expect(rulerGroups([first, second])).toEqual([
      { indices: [0], meters: 100 },
      { indices: [1], meters: 100 },
    ]);
  });
});
