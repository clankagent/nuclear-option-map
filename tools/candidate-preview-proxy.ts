import { Agent, request } from 'node:http';
import type { IncomingMessage, ServerResponse } from 'node:http';

export const CANDIDATE_PREFIX = '/maps/candidate-v8/';
export function candidateAssetPath(raw: string): string | undefined {
  const path = raw.split(/[?#]/, 1)[0];
  // Reject encoded separators/traversal instead of normalizing them.
  if (path.includes('%') || path.includes('\\')) return;
  if (/^\/(heartland|ignus)\/(pyramid\.json|overview\.png)$/.test(path) ||
      /^\/incremental\/(coverage\.json|(?:heartland|ignus)\/10\/[0-9]+\/[0-9]+\.webp)$/.test(path) ||
      /^\/(heartland|ignus)\/[0-9]\/[0-9]+\/[0-9]+\.webp$/.test(path) ||
      /^\/candidate-source\/datasets\/(heartland|ignus)\/10\/[0-9]+\/[0-9]+\.webp$/.test(path)) return path;
}

/** Opt-in static candidate proxy. Fixed loopback origin; no arbitrary targets. */
export function createCandidatePreviewProxy(value: string | undefined) {
  if (!value) return undefined;
  const target = new URL(value);
  if (target.protocol !== 'http:' || target.hostname !== '127.0.0.1' || !target.port || target.username || target.password || target.pathname !== '/' || target.search || target.hash) throw new Error('Candidate preview requires an explicit http://127.0.0.1:PORT origin');
  const agent = new Agent({ keepAlive: false, maxSockets: 4 });
  let active = 0;
  const waiting: (() => void)[] = [];
  const admit = () => { while (active < 4 && waiting.length) waiting.shift()!(); };
  return (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    const raw = req.url ?? '';
    if (!raw.startsWith(CANDIDATE_PREFIX)) { next(); return; }
    const fail = (status: number) => { res.writeHead(status, { 'Cache-Control': 'no-store' }); res.end(); };
    if (req.method !== 'GET' && req.method !== 'HEAD') { res.setHeader('Allow', 'GET, HEAD'); fail(405); return; }
    const path = candidateAssetPath('/' + raw.slice(CANDIDATE_PREFIX.length));
    if (!path) { fail(404); return; }
    if (active >= 4 && waiting.length >= 16) { fail(503); return; }
    let upstream: ReturnType<typeof request> | undefined;
    let started = false, closed = false, expired = false;
    const deadline = Date.now() + 15000;
    const dequeue = () => { const index = waiting.indexOf(start); if (index >= 0) waiting.splice(index, 1); };
    const start = () => {
      if (closed || expired || res.writableEnded || res.destroyed) return;
      if (Date.now() >= deadline) { expired = true; if (!res.headersSent) fail(504); else res.destroy(); return; }
      started = true; active++;
      upstream = request(target, { path, method: req.method, agent, timeout: 10000 }, incoming => {
        if (closed || expired || res.writableEnded || res.destroyed) { incoming.destroy(); return; }
        const size = Number(incoming.headers['content-length']);
        if (!Number.isSafeInteger(size) || size < 0 || size > 4 * 1024 * 1024) { incoming.destroy(); upstream!.destroy(); fail(502); return; }
        res.writeHead(incoming.statusCode ?? 502, { 'Content-Type': incoming.headers['content-type'] ?? 'application/octet-stream', 'Content-Length': size, 'Cache-Control': 'no-store' });
        incoming.on('error', () => res.destroy()); incoming.pipe(res);
      });
      upstream.on('timeout', () => upstream!.destroy(new Error('Candidate upstream timeout')));
      upstream.on('error', () => { if (!res.headersSent && !closed && !expired && !res.writableEnded && !res.destroyed) fail(502); else res.destroy(); });
      upstream.end();
    };
    // The absolute deadline includes FIFO waiting time; no unbounded admission.
    const timer = setTimeout(() => {
      expired = true; dequeue(); // Synchronous removal precedes end/async close.
      upstream?.destroy(new Error('Candidate request deadline'));
      if (!res.headersSent && !res.writableEnded && !res.destroyed) fail(504); else res.destroy();
    }, 15000);
    res.on('close', () => {
      if (closed) return;
      closed = true; clearTimeout(timer); upstream?.destroy();
      dequeue();
      if (started) active--;
      admit();
    });
    waiting.push(start); admit();
  };
}
