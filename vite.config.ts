import { defineConfig } from "vite-plus";
import { cpSync, existsSync } from "node:fs";
import { basename, resolve } from "node:path";
import { createMapStaticMiddleware } from "./tools/map-static-middleware";
import { createCandidatePreviewProxy } from "./tools/candidate-preview-proxy";
export { mapRasterCacheControl } from "./tools/map-static-middleware";

export default defineConfig(({ mode }) => {
  const packageAssets = mode === "package";
  const output = packageAssets ? "dist/demo-package" : "dist/app";
  let source = "";
  let destination = "";
  return {
    root: "demo",
    server: {
      host: "127.0.0.1",
      port: 47832,
      strictPort: true,
      allowedHosts: ["nuclear-map.localhost", "nuclear-map.agent.dikka.dev"],
      fs: { allow: [".."] },
      // Generated local datasets contain tens of thousands of tiles and may be baking.
      // Serve/refetch them normally without enrolling every directory in HMR watching.
      watch: { ignored: ["**/work/**", "**/demo/public/maps/**"] },
    },
    build: {
      outDir: `../${output}`,
      emptyOutDir: true,
      copyPublicDir: false,
      rollupOptions: {
        input: { map: resolve("demo/index.html"), status: resolve("demo/status/index.html") },
      },
    },
    plugins: [
      {
        name: "serve-generated-map-assets",
        configureServer(server) {
          const candidate = createCandidatePreviewProxy(
            process.env.NUCLEAR_MAP_CANDIDATE_PREVIEW_ORIGIN,
          );
          if (candidate) server.middlewares.use(candidate);
          server.middlewares.use(
            createMapStaticMiddleware(resolve(server.config.publicDir, "maps")),
          );
        },
      },
      {
        name: "copy-demo-map-views",
        apply: "build",
        configResolved(config) {
          source = config.publicDir;
          destination = config.build.outDir;
        },
        closeBundle() {
          if (packageAssets && source && existsSync(source))
            cpSync(source, destination, {
              recursive: true,
              filter: (path) => basename(path) !== "geometry" && !basename(path).startsWith("."),
            });
        },
      },
    ],
    test: { root: ".", include: ["tests/browser/**/*.test.ts"] },
  };
});
