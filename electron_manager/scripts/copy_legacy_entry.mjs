import { readFile } from "node:fs/promises";
import { build } from "esbuild";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptsDir = path.dirname(fileURLToPath(import.meta.url));
const projectDir = path.resolve(scriptsDir, "..");
const outputDir = path.join(projectDir, "dist-electron");

const entryPoint = path.join(projectDir, "electron", "main.ts");

await build({
  entryPoints: [entryPoint],
  outfile: path.join(outputDir, "legacy-main.cjs"),
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node16",
  external: ["electron"],
  plugins: [{
    name: "commonjs-main-entry",
    setup(buildContext) {
      buildContext.onLoad({ filter: /main\.ts$/ }, async (args) => ({
        contents: (await readFile(args.path, "utf8")).replace(
          "fileURLToPath(import.meta.url)",
          "__filename",
        ),
        loader: "ts",
      }));
    },
  }],
});
