import { defineConfig } from 'vite-plus';

export default defineConfig({
  root: 'demo',
  server: {
    host: '127.0.0.1',
    port: 47832,
    strictPort: true,
    allowedHosts: ['nuclear-map.localhost', 'nuclear-map.agent.dikka.dev'],
    fs: { allow: ['..'] },
  },
  build: { outDir: '../dist/demo', emptyOutDir: true },
  test: { root: '.', include: ['tests/browser/**/*.test.ts'] },
});
