import { defineConfig } from 'vite-plus';
import { cpSync, existsSync } from 'node:fs';
import { basename, resolve } from 'node:path';

export default defineConfig({
  root: 'demo',
  server: {
    host: '127.0.0.1',
    port: 47832,
    strictPort: true,
    allowedHosts: ['nuclear-map.localhost', 'nuclear-map.agent.dikka.dev'],
    fs: { allow: ['..'] },
  },
  build: { outDir: '../dist/demo', emptyOutDir: true, copyPublicDir: false },
  plugins: [{
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
