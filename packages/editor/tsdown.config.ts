import { defineConfig } from "tsdown";

export default defineConfig([{
  entry: ["src/index.ts", "src/lib.ts"],
  format: ["esm"],
  dts: true,
  copy: ["src/styles"],
  exports: {
    customExports: (exports) => ({
      ...exports,
      "./styles.css": "./dist/styles/index.css",
      "./styles": "./dist/styles/index.css",
    }),
  },
  platform: "neutral",
}, {
  entry: { "project-bundle-codec": "src/core/helpers/project-bundle-cli.ts" },
  format: ["esm"],
  platform: "node",
  dts: false,
  exports: false,
  clean: false,
  noExternal: [/.*/],
  outExtensions: () => ({ js: ".mjs" }),
  banner: 'import { createRequire as __codecCreateRequire } from "node:module"; const require = __codecCreateRequire(import.meta.url);',
}]);
