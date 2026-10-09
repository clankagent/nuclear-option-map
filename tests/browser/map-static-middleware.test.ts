import { expect, it } from 'vite-plus/test';
import { mkdtemp, mkdir, rm, symlink, writeFile } from 'node:fs/promises';
import { createServer, request } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createMapStaticMiddleware } from '../../tools/map-static-middleware';

it('serves newly generated map assets directly and confines lookups to visible map files', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'nuclear-map-static-'));
  const maps = join(scratch, 'maps');
  await mkdir(maps);
  const middleware = createMapStaticMiddleware(maps);
  const server = createServer((req, res) => {
    void middleware(req, res, () => { res.setHeader('Content-Type', 'text/html'); res.end('fallback'); });
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('No test server port');
  const get = (path: string, method = 'GET') => new Promise<{ status: number; headers: import('node:http').IncomingHttpHeaders; body: Buffer }>((resolve, reject) => {
    const req = request({ host: '127.0.0.1', port: address.port, path, method }, res => {
      const chunks: Buffer[] = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => resolve({ status: res.statusCode!, headers: res.headers, body: Buffer.concat(chunks) }));
    });
    req.on('error', reject); req.end();
  });
  try {
    // Create both the nested directory and asset only after the middleware/server start.
    const tiles = join(maps, 'tiles', 'new-map');
    await mkdir(tiles, { recursive: true });
    const bytes = Buffer.from([0x52, 0x49, 0x46, 0x46, 0, 1, 2, 3]);
    await writeFile(join(tiles, 'overview.webp'), bytes);
    const path = '/maps/tiles/new-map/overview.webp?v=edd944ed441b1903';
    const found = await get(path);
    expect(found.status).toBe(200);
    expect(found.body).toEqual(bytes);
    expect(found.headers['content-type']).toBe('image/webp');
    expect(found.headers['cache-control']).toBe('public, max-age=31536000, immutable');
    expect(found.headers['content-length']).toBe(String(bytes.length));
    const head = await get(path, 'HEAD');
    expect(head.status).toBe(200);
    expect(head.body.length).toBe(0);
    expect(head.headers['content-length']).toBe(String(bytes.length));
    await writeFile(join(maps, 'manifest.json'), '{"maps":[]}');
    const catalog = await get('/maps/manifest.json');
    expect(catalog.headers['content-type']).toBe('application/json');
    expect(catalog.headers['cache-control']).toBe('no-cache');
    expect(catalog.body.toString()).toBe('{"maps":[]}');
    const outside = join(scratch, 'outside');
    await mkdir(outside);
    await writeFile(join(outside, 'secret.webp'), bytes);
    await symlink(outside, join(maps, 'escape'), 'junction');
    await mkdir(join(maps, '.render-work'));
    await writeFile(join(maps, '.render-work', 'preview.webp'), bytes);
    for (const forbidden of [
      '/maps/tiles/new-map/missing.webp?v=edd944ed441b1903',
      '/maps/../outside/secret.webp', '/maps/%2e%2e/outside/secret.webp',
      '/maps/tiles/%2e%2e%2fmanifest.json', '/maps/%5coutside/secret.webp',
      '/maps/.render-work/preview.webp', '/maps/%2erender-work/preview.webp',
      '/maps/escape/secret.webp', '/maps/%ZZ', '/maps/tiles/new-map/',
    ]) {
      const missing = await get(forbidden);
      expect(missing.status, forbidden).toBe(404);
      expect(missing.headers['cache-control'], forbidden).toBe('no-store');
      expect(missing.headers['content-type']).not.toContain('text/html');
    }
    expect((await get('/unrelated')).body.toString()).toBe('fallback');
    expect((await get(path, 'POST')).status).toBe(405);
  } finally {
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    await rm(scratch, { recursive: true, force: true });
  }
});
