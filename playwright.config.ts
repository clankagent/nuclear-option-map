import { defineConfig } from "@playwright/test";

const port = Number(process.env.NUCLEAR_MAP_SMOKE_PORT ?? 47839);

export default defineConfig({
  testDir: "./tests/smoke",
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 30_000,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
    },
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1280, height: 800 } } },
    {
      name: "phone",
      use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
    },
  ],
  webServer: {
    command: `node node_modules/vite-plus/bin/vp preview --outDir ../dist/app --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
