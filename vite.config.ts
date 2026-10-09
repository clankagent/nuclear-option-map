import { defineConfig } from 'vite-plus';
import { cpSync, existsSync } from 'node:fs';
import { basename, resolve } from 'node:path';
import { createMapStaticMiddleware } from './tools/map-static-middleware';
export { mapRasterCacheControl } from './tools/map-static-middleware';

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
    name: 'serve-generated-map-assets',
    configureServer(server) {
      server.middlewares.use(createMapStaticMiddleware(resolve(server.config.publicDir, 'maps')));
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
