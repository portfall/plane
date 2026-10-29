export default {
  entry: { "project-bundle-codec": "src/core/helpers/project-bundle-cli.ts" },
  format: ["esm"],
  platform: "node",
  dts: false,
  exports: false,
  noExternal: [/.*/],
  outDir: "/codec",
  outExtensions: () => ({ js: ".mjs" }),
  banner: 'import { createRequire as __codecCreateRequire } from "node:module"; const require = __codecCreateRequire(import.meta.url);',
};
