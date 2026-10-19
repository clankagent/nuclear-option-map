import { copyFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { root, runNode } from "./run-tool.mjs";

runNode("node_modules/vite-plus/bin/vp", ["build", "--config", "vite.lib.config.ts"]);
runNode("node_modules/typescript/bin/tsc", ["-p", "tsconfig.lib.json"]);
mkdirSync(resolve(root, "dist/wasm"), { recursive: true });
for (const name of [
  "nuclear_option_map.js",
  "nuclear_option_map.d.ts",
  "nuclear_option_map_bg.wasm",
  "nuclear_option_map_bg.wasm.d.ts",
]) {
  copyFileSync(resolve(root, "pkg", name), resolve(root, "dist/wasm", name));
}
