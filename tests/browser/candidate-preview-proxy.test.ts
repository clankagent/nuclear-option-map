import { expect, it, vi } from 'vite-plus/test';
import { EventEmitter } from 'node:events';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { createServer, request } from 'node:http';
import type { Server } from 'node:http';
import { candidateAssetPath, createCandidatePreviewProxy } from '../../tools/candidate-preview-proxy';

const listen = (s: Server) => new Promise<number>(resolve => s.listen(0, '127.0.0.1', () => { const a = s.address(); if (a && typeof a !== 'string') resolve(a.port); }));
const close = (s: Server) => new Promise<void>(resolve => s.close(() => resolve()));
it('requires explicit loopback origin and rejects noncandidate paths', () => {
  expect(createCandidatePreviewProxy(undefined)).toBeUndefined();
  for (const url of ['http://example.org:80', 'http://0.0.0.0:80', 'http://127.0.0.1:80/elsewhere', 'http://user:pass@127.0.0.1:80', 'https://127.0.0.1:80']) expect(() => createCandidatePreviewProxy(url)).toThrow();
  for (const p of ['/heartland/pyramid.json', '/ignus/9/22/3.webp', '/candidate-source/datasets/heartland/10/421/165.webp', '/incremental/coverage.json', '/incremental/ignus/10/120/1.webp']) expect(candidateAssetPath(p)).toBe(p);
  for (const p of ['/heartland/../candidate-complete.json', '/heartland/%2e%2e/pyramid.json', '/heartland/0/0%2f1/0.webp', '/heartland\\0/0/0.webp', '/heartland/10/0/0.webp', '/candidate-source/datasets/heartland/9/0/0.webp', '/heartland/', '/secret.json']) expect(candidateAssetPath(p)).toBeUndefined();
});
it('streams only whitelisted GET/HEAD to its fixed origin', async () => {
  const seen: string[] = [];
  const upstream = createServer((req, res) => { seen.push(req.url!); res.writeHead(200, { 'Content-Length': 4, 'Content-Type': 'image/webp' }); res.end(req.method === 'HEAD' ? undefined : 'tile'); });
  const port = await listen(upstream);
  const proxy = createCandidatePreviewProxy(`http://127.0.0.1:${port}`)!;
  const frontend = createServer((req, res) => proxy(req, res, () => { res.end('normal'); }));
  const local = await listen(frontend);
  const get = (path: string, method = 'GET') => new Promise<{ status: number; body: string }>((resolve, reject) => {
    const req = request({ host: '127.0.0.1', port: local, path, method }, res => { let body = ''; res.on('data', b => body += b); res.on('end', () => resolve({ status: res.statusCode!, body })); }); req.on('error', reject); req.end();
  });
  try {
    expect(await get('/maps/candidate-v8/heartland/0/0/0.webp')).toEqual({ status: 200, body: 'tile' });
    expect(await get('/maps/candidate-v8/heartland/0/0/0.webp', 'HEAD')).toEqual({ status: 200, body: '' });
    expect((await get('/maps/candidate-v8/heartland/%2e%2e/secret')).status).toBe(404);
    expect((await get('/maps/candidate-v8/heartland/0/0/0.webp', 'POST')).status).toBe(405);
    expect((await get('/maps/manifest.json')).body).toBe('normal');
    expect(seen).toEqual(['/heartland/0/0/0.webp', '/heartland/0/0/0.webp']);
  } finally { await close(frontend); await close(upstream); }
});

it('queues a viewer burst in FIFO order with four upstream requests and removes a canceled waiter', async () => {
  let concurrent = 0, peak = 0;
  const seen: string[] = [];
  const upstream = createServer((req, res) => {
    concurrent++; peak = Math.max(peak, concurrent); seen.push(req.url!);
    setTimeout(() => { concurrent--; res.writeHead(200, { 'Content-Length': 4 }); res.end('tile'); }, 40);
  });
  const port = await listen(upstream);
  const proxy = createCandidatePreviewProxy(`http://127.0.0.1:${port}`)!;
  let cancelArrived!: () => void;
  const arrived = new Promise<void>(resolve => { cancelArrived = resolve; });
  const frontend = createServer((req, res) => {
    proxy(req, res, () => res.end());
    if (req.url?.includes('/99.webp')) cancelArrived();
  });
  const local = await listen(frontend);
  const get = (index: number) => new Promise<number>((resolve, reject) => {
    const req = request({ host: '127.0.0.1', port: local, path: `/maps/candidate-v8/heartland/0/0/${index}.webp` }, res => { res.resume(); res.on('end', () => resolve(res.statusCode!)); });
    req.on('error', reject); req.end();
  });
  try {
    // The real viewer permits six in-flight loads; exercise a larger eight-tile burst.
    const burst = Array.from({ length: 8 }, (_, i) => get(i));
    const canceled = request({ host: '127.0.0.1', port: local, path: '/maps/candidate-v8/heartland/0/0/99.webp' });
    canceled.on('error', () => {}); canceled.end();
    await arrived; canceled.destroy();
    expect(await Promise.all(burst)).toEqual(Array(8).fill(200));
    expect(peak).toBe(4);
    expect(seen).toEqual(Array.from({ length: 8 }, (_, i) => `/heartland/0/0/${i}.webp`));
    expect(await get(10)).toBe(200);
  } finally { await close(frontend); await close(upstream); }
});

it('cannot admit an expired waiter before its response close event', async () => {
  let seen = 0, fourArrived!: () => void;
  const arrived = new Promise<void>(resolve => { fourArrived = resolve; });
  const upstream = createServer((_req, _res) => { if (++seen === 4) fourArrived(); });
  const port = await listen(upstream);
  const proxy = createCandidatePreviewProxy(`http://127.0.0.1:${port}`)!;
  class Response extends EventEmitter {
    headersSent = false; writableEnded = false; destroyed = false; status = 0;
    writeHead(status: number) { this.status = status; this.headersSent = true; }
    end() { this.writableEnded = true; } // Deliberately defer close to reproduce the race.
    destroy() { this.destroyed = true; this.emit('close'); }
  }
  const responses: Response[] = [];
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
  try {
    for (let i = 0; i < 5; i++) {
      const res = new Response(); responses.push(res);
      proxy({ url: `/maps/candidate-v8/heartland/0/0/${i}.webp`, method: 'GET' } as IncomingMessage, res as unknown as ServerResponse, () => {});
    }
    await arrived;
    vi.advanceTimersByTime(15000);
    expect(responses[4].status).toBe(504);
    // Free a slot while the expired queued response has not emitted close.
    responses[0].emit('close');
    vi.useRealTimers();
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(seen).toBe(4);
  } finally {
    vi.useRealTimers();
    for (const res of responses) res.emit('close');
    await close(upstream);
  }
});
