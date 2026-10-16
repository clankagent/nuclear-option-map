import { expect, it } from 'vite-plus/test';
import { cellAt, clampZoom, describeCell, keyAction, matchState, moveCell, stepZoom } from '../../demo/status/coverage';
import { isStale, parseStatus, percent, safeHref } from '../../demo/status/model';

const at = Date.parse('2026-10-15T15:00:00Z');
const snapshot = () => ({ schemaVersion: 1, job: { id: 'job', title: 'Current work' }, state: 'running',
  generatedAt: new Date(at).toISOString(), sourceGeneratedAt: new Date(at - 30000).toISOString(), censusGeneratedAt: new Date(at - 100000).toISOString(),
  collector: { ok: true, staleAfterSeconds: 180 }, progress: { unit: 'files', completed: 100, total: 100 },
  stages: [], groups: [], completion: { datasetComplete: false } });

it('keeps 100 percent metadata counts separate from verified job completion', () => {
  const data = parseStatus(snapshot());
  expect(percent(data.progress.completed!, data.progress.total)).toBe(100);
  expect(data.state).toBe('running');
  expect(data.completion.datasetComplete).toBe(false);
  expect(() => parseStatus({ ...snapshot(), state: 'complete' })).toThrow();
  expect(parseStatus({ ...snapshot(), state: 'complete', completion: { datasetComplete: true } }).state).toBe('complete');
});

it('checks source and census ages independently of a fresh local file rewrite', () => {
  const data = parseStatus(snapshot());
  expect(isStale(data, at)).toBe(false);
  expect(isStale({ ...data, sourceGeneratedAt: new Date(at - 91000).toISOString() }, at)).toBe(true);
  expect(isStale({ ...data, censusGeneratedAt: new Date(at - 181000).toISOString() }, at)).toBe(true);
  expect(isStale({ ...data, sourceGeneratedAt: null }, at)).toBe(true);
});

const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAHnOcQAAAAABJRU5ErkJggg==';
const states = [
  { code: 0, label: 'Unchanged', color: '#252b32', count: 1 },
  { code: 3, label: 'Collected', color: '#6fd8b0', count: 0 },
];
const cmap = (over: Record<string, unknown> = {}) => ({ id: 'heartland', label: 'Heartland', width: 1, height: 1, image: PNG, states, measuredAt: null, cellMeters: 128, provisional: true, ...over });

it('parses optional coverage maps and stays compatible with old snapshots', () => {
  expect(parseStatus(snapshot()).coverageMaps).toEqual([]);
  expect(parseStatus({ ...snapshot(), state: 'preparing' }).state).toBe('preparing');
  const maps = parseStatus({ ...snapshot(), coverageMaps: [cmap(), cmap({ id: 'ignus', label: 'Ignus' }), cmap({ id: 'third' })] }).coverageMaps;
  expect(maps.map((m) => m.id)).toEqual(['heartland', 'ignus']);
  expect(parseStatus({ ...snapshot(), coverageMaps: 'nope' }).coverageMaps).toEqual([]);
});

it('rejects unsafe coverage images, dimensions, colors and state counts per map', () => {
  const bad = [
    cmap({ image: 'data:image/svg+xml;base64,PHN2Zz4=' }),
    cmap({ image: 'https://example.com/a.png' }),
    cmap({ image: 'data:image/png;base64,AAAA' }),
    cmap({ image: PNG + '"onerror=x' }),
    cmap({ image: 'data:image/png;base64,iVBORw0KGgo' + 'A'.repeat(2 * 1024 * 1024) }),
    cmap({ width: 0 }), cmap({ width: 2049 }), cmap({ height: 1.5 }),
    cmap({ width: 640 }), // Actual PNG dimensions must agree before bitmap decoding.
    cmap({ states: [{ ...states[0], count: .5 }] }),
    cmap({ states: [{ ...states[0], count: 2 }] }),
    cmap({ states: [states[0], { ...states[1], code: 0 }] }),
    cmap({ states: [states[0], { ...states[1], color: states[0]!.color }] }),
    cmap({ states: Array.from({ length: 9 }, (_, i) => ({ ...states[0], code: i })) }),
    cmap({ states: [{ ...states[0], color: 'red' }] }),
    cmap({ states: [{ ...states[0], color: '#fff' }] }),
    cmap({ cellMeters: 64 }), cmap({ provisional: false }),
  ];
  for (const m of bad) expect(parseStatus({ ...snapshot(), coverageMaps: [m, cmap({ id: 'ok' })] }).coverageMaps.map((x) => x.id)).toEqual(['ok']);
});

it('maps pointer positions and palette colors to tiles and states', () => {
  const map = { width: 640, height: 640 };
  expect(cellAt(0, 0, 640, 640, map)).toEqual({ col: 0, row: 0 });
  expect(cellAt(1279, 640, 1280, 1280, map)).toEqual({ col: 639, row: 320 });
  expect(cellAt(1280, 0, 1280, 1280, map)).toBeNull();
  expect(matchState(0x6f, 0xd8, 0xb0, 255, states)?.label).toBe('Collected');
  expect(matchState(0x6f, 0xd8, 0xb1, 255, states)).toBeNull();
  expect(matchState(0x6f, 0xd8, 0xb0, 0, states)).toBeNull();
  expect(describeCell(parseStatus({ ...snapshot(), coverageMaps: [cmap()] }).coverageMaps[0]!, { col: 2, row: 3 }, states[1]!)).toContain('256 m east and 384 m south');
});

it('keeps zoom inside 1 to 8 and routes keyboard controls', () => {
  expect(clampZoom(0)).toBe(1);
  expect(clampZoom(99)).toBe(8);
  expect(stepZoom(1, -1)).toBe(1);
  expect(stepZoom(8, 1)).toBe(8);
  expect(stepZoom(1, 1)).toBe(2);
  expect(stepZoom(4, -1)).toBe(3);
  expect(keyAction('+')).toBe('zoom-in');
  expect(keyAction('-')).toBe('zoom-out');
  expect(keyAction('0')).toBe('reset');
  expect(keyAction('ArrowLeft')).toBe('move');
  expect(keyAction('Tab')).toBeNull();
  const map = { width: 4, height: 4 };
  expect(moveCell(null, 'ArrowLeft', map)).toEqual({ col: 2, row: 2 });
  expect(moveCell({ col: 0, row: 0 }, 'ArrowLeft', map)).toEqual({ col: 0, row: 0 });
  expect(moveCell({ col: 3, row: 3 }, 'End', map)).toEqual({ col: 3, row: 3 });
});

it('rejects malformed data and executable or normalized external link tricks', () => {
  expect(() => parseStatus({ ...snapshot(), schemaVersion: 99 })).toThrow();
  expect(() => parseStatus({ ...snapshot(), progress: { total: -1 } })).toThrow();
  for (const href of ['javascript:alert(1)', '//example.com', '/\\example.com', '/\n/example.com', 'data:text/html,hi']) expect(safeHref(href)).toBeNull();
  expect(safeHref('/status/')).toBe('/status/');
  expect(safeHref('https://example.com/status')).toBe('https://example.com/status');
});
