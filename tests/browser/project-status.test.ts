import { expect, it } from 'vite-plus/test';
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

it('rejects malformed data and executable or normalized external link tricks', () => {
  expect(() => parseStatus({ ...snapshot(), schemaVersion: 99 })).toThrow();
  expect(() => parseStatus({ ...snapshot(), progress: { total: -1 } })).toThrow();
  for (const href of ['javascript:alert(1)', '//example.com', '/\\example.com', '/\n/example.com', 'data:text/html,hi']) expect(safeHref(href)).toBeNull();
  expect(safeHref('/status/')).toBe('/status/');
  expect(safeHref('https://example.com/status')).toBe('https://example.com/status');
});
