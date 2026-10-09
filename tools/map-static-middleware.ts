import { createReadStream } from 'node:fs';
import { realpath, stat } from 'node:fs/promises';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { extname, isAbsolute, relative, resolve, sep } from 'node:path';

/** Versioned raster URLs are replaced when their generated dataset changes. */
export function mapRasterCacheControl(rawUrl: string): string | undefined {
  const url = new URL(rawUrl, 'http://localhost');
  if (/^\/maps\/(?:tiles|terrain)\/[^?#]+\.(?:webp|png)$/.test(url.pathname) && /^[a-f0-9]{8,64}$/i.test(url.searchParams.get('v') ?? '')) return 'public, max-age=31536000, immutable';
}

const mimeTypes: Record<string, string> = {
  '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.gif': 'image/gif', '.svg': 'image/svg+xml', '.avif': 'image/avif',
  '.json': 'application/json', '.bin': 'application/octet-stream', '.wasm': 'application/wasm',
  '.glb': 'model/gltf-binary', '.gltf': 'model/gltf+json', '.txt': 'text/plain; charset=utf-8',
};

function within(root: string, file: string): boolean {
  const path = relative(root, file);
  return path !== '' && !isAbsolute(path) && path !== '..' && !path.startsWith(`..${sep}`);
}

/** Direct lookups also find tiles baked after Vite's public-file inventory was created. */
export function createMapStaticMiddleware(mapsRoot: string) {
  const root = resolve(mapsRoot);
  return async (req: IncomingMessage, res: ServerResponse, next: () => void): Promise<void> => {
    const rawUrl = req.url ?? '/';
    const rawPath = rawUrl.split(/[?#]/, 1)[0];
    if (!rawPath.startsWith('/maps/')) { next(); return; }
    const fail = (status: number) => {
      res.statusCode = status;
      res.setHeader('Cache-Control', 'no-store');
      res.setHeader('Content-Type', 'text/plain; charset=utf-8');
      res.end(req.method === 'HEAD' ? undefined : 'Map asset unavailable');
    };
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.setHeader('Allow', 'GET, HEAD'); fail(405); return;
    }
    try {
      const segments = rawPath.slice('/maps/'.length).split('/').map(segment => decodeURIComponent(segment));
      if (segments.some(segment => !segment || segment.startsWith('.') || /[\\/:\x00]/.test(segment))) {
        fail(404); return;
      }
      const file = resolve(root, ...segments);
      if (!within(root, file)) { fail(404); return; }
      const [realRoot, realFile] = await Promise.all([realpath(root), realpath(file)]);
      if (!within(realRoot, realFile) || relative(realRoot, realFile).split(sep).some(segment => segment.startsWith('.'))) {
        fail(404); return;
      }
      const metadata = await stat(realFile);
      if (!metadata.isFile()) { fail(404); return; }
      res.setHeader('Content-Type', mimeTypes[extname(file).toLowerCase()] ?? 'application/octet-stream');
      res.setHeader('Content-Length', metadata.size);
      res.setHeader('Last-Modified', metadata.mtime.toUTCString());
      res.setHeader('Cache-Control', mapRasterCacheControl(rawUrl) ?? 'no-cache');
      if (req.method === 'HEAD') { res.end(); return; }
      const stream = createReadStream(realFile);
      stream.on('error', () => {
        if (res.headersSent) res.destroy();
        else { res.removeHeader('Content-Length'); fail(404); }
      });
      stream.pipe(res);
    } catch { fail(404); }
  };
}
