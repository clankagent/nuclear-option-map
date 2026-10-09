import { defineConfig } from 'vite-plus';

export default defineConfig({
  publicDir: false,
  build: {
    outDir: 'dist/browser',
    emptyOutDir: true,
    lib: { entry: 'src/index.ts', formats: ['es'], fileName: 'index' },
  },
});
