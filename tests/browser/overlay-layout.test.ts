import { describe, expect, it } from 'vite-plus/test';
import { placeLabels } from '../../demo/ui/overlay-layout';

describe('overlay label placement', () => {
  it('keeps priority labels and rejects ordinary overlaps', () => {
    const placed = placeLabels([
      { id: 'ordinary', x: 0, y: 0, width: 20, height: 20 },
      { id: 'selected', x: 5, y: 5, width: 20, height: 20, priority: 1 },
      { id: 'clear', x: 30, y: 0, width: 20, height: 20 },
    ]);
    expect(placed.map(item => item.id)).toEqual(['selected', 'clear']);
  });
});
