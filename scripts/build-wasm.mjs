import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { root, run } from "./run-tool.mjs";

const version = "0.2.129";
const local = resolve(
  root,
  `tools/bin/wasm-bindgen-${version}-x86_64-pc-windows-msvc/wasm-bindgen.exe`,
);
const tool = process.platform === "win32" && existsSync(local) ? local : "wasm-bindgen";
const check = spawnSync(tool, ["--version"], { cwd: root, encoding: "utf8", windowsHide: true });
if (check.status !== 0 || check.stdout.trim() !== `wasm-bindgen ${version}`) {
  throw new Error(
    `Install wasm-bindgen-cli ${version}: cargo install wasm-bindgen-cli --version ${version} --locked`,
  );
}
run("cargo", [
  "build",
  "--locked",
  "--release",
  "-p",
  "nuclear-option-map-wasm",
  "--target",
  "wasm32-unknown-unknown",
]);
const target = resolve(root, process.env.CARGO_TARGET_DIR ?? "target");
run(tool, [
  resolve(target, "wasm32-unknown-unknown/release/nuclear_option_map_wasm.wasm"),
  "--target",
  "web",
  "--out-dir",
  resolve(root, "pkg"),
  "--out-name",
  "nuclear_option_map",
]);
