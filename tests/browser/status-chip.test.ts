import { describe, expect, it } from 'vite-plus/test';
import { statusChip } from '../../demo/ui/status-chip';

describe('demo status chip', () => {
  const now = Date.parse('2026-10-19T15:00:00Z');
  const snapshot = { state: 'running', generatedAt: '2026-10-19T14:59:00Z', collector: { ok: true, staleAfterSeconds: 180 } };
  it('shows green only for fresh running and complete snapshots', () => {
    expect(statusChip(snapshot, now).tone).toBe('live');
    expect(statusChip({ ...snapshot, state: 'complete' }, now).tone).toBe('live');
    expect(statusChip({ ...snapshot, state: 'preparing' }, now).tone).toBe('off');
  });
  it('shows amber for stale or unhealthy snapshots', () => {
    expect(statusChip(snapshot, now + 181000)).toEqual({ text: 'Status · stale', tone: 'warn' });
    expect(statusChip({ ...snapshot, collector: { ok: false } }, now).tone).toBe('warn');
    for (const state of ['blocked','review','stale']) expect(statusChip({ ...snapshot, state }, now).tone).toBe('warn');
  });
  it('handles unavailable and malformed metadata', () => {
    for (const value of [null, {}, { state: 'running' }, { ...snapshot, generatedAt: 'bad' }, { ...snapshot, state: '<script>' }]) expect(statusChip(value, now).tone).toBe('off');
  });
});
