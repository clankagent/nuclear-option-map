import { defineConfig } from 'vite-plus';
import { cpSync, existsSync } from 'node:fs';
import { basename, resolve } from 'node:path';

/** Versioned raster URLs are replaced when their generated dataset changes. */
export function mapRasterCacheControl(rawUrl: string): string | undefined {
  const url = new URL(rawUrl, 'http://localhost');
  if (/^\/maps\/(?:tiles|terrain)\/[^?#]+\.(?:webp|png)$/.test(url.pathname) && /^[a-f0-9]{8,64}$/i.test(url.searchParams.get('v') ?? '')) return 'public, max-age=31536000, immutable';
}

export default defineConfig({
  root: 'demo',
  server: {
    host: '127.0.0.1',
    port: 47832,
    strictPort: true,
    allowedHosts: ['nuclear-map.localhost', 'nuclear-map.agent.dikka.dev'],
    fs: { allow: ['..'] },
    // Generated local datasets contain tens of thousands of tiles and may be baking.
    // Serve/refetch them normally without enrolling every directory in HMR watching.
    watch: { ignored: ['**/work/**', '**/demo/public/maps/**'] },
  },
  build: { outDir: '../dist/demo', emptyOutDir: true, copyPublicDir: false },
  plugins: [{
    name: 'cache-versioned-map-rasters',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const cacheControl = mapRasterCacheControl(req.url ?? '/');
        if (cacheControl) res.setHeader('Cache-Control', cacheControl);
        next();
      });
    },
  }, {
    name: 'copy-demo-map-views',
    apply: 'build',
    closeBundle() {
      const source = resolve('demo/public');
      if (existsSync(source)) cpSync(source, resolve('dist/demo'), {
        recursive: true,
        filter: path => basename(path) !== 'geometry' && !basename(path).startsWith('.'),
      });
    },
  }],
  test: { root: '.', include: ['tests/browser/**/*.test.ts'] },
});
