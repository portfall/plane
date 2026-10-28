import { cpSync, mkdirSync, readFileSync, readlinkSync, realpathSync, symlinkSync, unlinkSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";

const [source, target] = process.argv.slice(2).map((path) => resolve(path));
if (!source || !target || process.argv.length !== 4) throw new Error("usage: prepare-node-deploy.mjs source target");
const app = JSON.parse(readFileSync(join(target, "package.json"), "utf8")).name;
if (app !== "live" && app !== "space") throw new Error("unexpected deployment app");
const self = join(target, "node_modules/.pnpm/node_modules", app);
if (realpathSync(self) !== join(source, "apps", app)) throw new Error("unexpected deployment self link");
unlinkSync(self);
symlinkSync("../../..", self);

if (app === "live") {
  if (process.platform !== "linux" || !["arm64", "x64"].includes(process.arch)) {
    throw new Error("unreviewed runtime platform");
  }
  const store = join(source, "node_modules/.pnpm");
  const original = dirname(realpathSync(join(source, "apps/live/node_modules/sharp")));
  const deployed = dirname(realpathSync(join(target, "node_modules/sharp")));
  mkdirSync(join(deployed, "@img"), { recursive: true });
  for (const name of [`sharp-linuxmusl-${process.arch}`, `sharp-libvips-linuxmusl-${process.arch}`]) {
    const from = join(original, "@img", name);
    const entry = relative(store, realpathSync(from)).split(sep)[0];
    if (!entry.startsWith(`@img+${name}@`)) throw new Error("unexpected Sharp package location");
    cpSync(join(store, entry), join(target, "node_modules/.pnpm", entry), {
      recursive: true,
      force: false,
      errorOnExist: true,
      verbatimSymlinks: true,
    });
    symlinkSync(readlinkSync(from), join(deployed, "@img", name));
  }
}
