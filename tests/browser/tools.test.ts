import { describe, expect, it } from 'vite-plus/test';
import { addMeasurePoint, finishMeasure, hitPin, hitPoint, initialToolState, removeMeasurePoint, selectTool, UndoStack } from '../../demo/ui/tools';

describe('demo tool state', () => {
  it('deduplicates the second pointer-up of a double-click in CSS pixels, then finishes without adding a leg', () => {
    const screen = (p: { x: number; z: number }) => ({ x: p.x / 100, z: p.z / 100 });
    let state = addMeasurePoint(initialToolState(), { x: 0, z: 0 }, screen);
    state = addMeasurePoint(state, { x: 1000, z: 2000 }, screen);
    expect(addMeasurePoint(state, { x: 1000, z: 2000 }, screen)).toBe(state);
    expect(addMeasurePoint(state, { x: 1200, z: 2200 }, screen)).toBe(state);
    const finished = finishMeasure(state);
    expect(finished.points).toEqual(state.points);
    expect(finished.points).toHaveLength(2);
    expect(finished.finished).toBe(true);
    expect(addMeasurePoint(state, { x: 1400, z: 2000 }, screen).points).toHaveLength(3);
  });
  it('starts in navigate and opens a fresh route after finishing', () => {
    let state = selectTool(initialToolState(), 'measure');
    state = addMeasurePoint(state, { x: 0, z: 0 }); state = addMeasurePoint(state, { x: 3, z: 4 });
    expect(state.points).toHaveLength(2);
    state = { ...state, finished: true };
    expect(addMeasurePoint(state, { x: 8, z: 9 }).points).toEqual([{ x: 8, z: 9 }]);
    expect(removeMeasurePoint(state).points).toHaveLength(1);
  });
  it('hit tests pins and edit handles in screen pixels', () => {
    const pins = [{ id: 'a', name: 'A', x: 20, z: 40, color: 'green' as const }];
    const screen = (p: { x: number; z: number }) => p;
    expect(hitPin(pins, screen, 28, 40, 12)?.id).toBe('a');
    expect(hitPin(pins, screen, 33, 40, 12)).toBeNull();
    expect(hitPoint(pins, screen, 20, 50, 10)).toBe(0);
  });
  it('bounds undo history at twenty actions', () => {
    const stack = new UndoStack();
    for (let i = 0; i < 25; i++) stack.push({ kind: 'measure', before: [{ x: i, z: 0 }], finished: false });
    expect(stack.length).toBe(20);
    for (let i = 24; i >= 5; i--) expect(stack.pop()).toMatchObject({ before: [{ x: i }] });
    expect(stack.pop()).toBeUndefined();
  });
});
