import { createHash } from "node:crypto";
import { lstatSync, readdirSync, readFileSync, readlinkSync, realpathSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

function files(directory, prefix = "") {
  return readdirSync(directory, { withFileTypes: true })
    .flatMap((entry) => {
      if (entry.name === "node_modules") return [];
      const name = join(prefix, entry.name);
      return entry.isDirectory() ? files(join(directory, entry.name), name) : [name];
    })
    .toSorted();
}

export function fingerprint(directory) {
  const hash = createHash("sha256");
  for (const name of files(directory)) {
    const path = join(directory, name);
    const isLink = lstatSync(path).isSymbolicLink();
    const data = isLink ? Buffer.from(readlinkSync(path)) : readFileSync(path);
    hash
      .update(name)
      .update("\0")
      .update(isLink ? "link" : "file")
      .update("\0")
      .update(String(data.length))
      .update("\0")
      .update(data);
  }
  return hash.digest("hex");
}

export function packages(directory) {
  const store = join(directory, "node_modules/.pnpm");
  const result = [];
  for (const entry of readdirSync(store, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name === "node_modules") continue;
    const modules = join(store, entry.name, "node_modules");
    for (const item of readdirSync(modules, { withFileTypes: true })) {
      if (!item.isDirectory()) continue;
      if (item.name.startsWith("@")) {
        for (const child of readdirSync(join(modules, item.name), { withFileTypes: true })) {
          if (child.isDirectory()) result.push(join(modules, item.name, child.name));
        }
      } else result.push(join(modules, item.name));
    }
  }
  return result;
}

function checkLinks(directory, root = realpathSync(directory)) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isSymbolicLink()) {
      const target = realpathSync(path);
      if (target !== root && !target.startsWith(root + sep)) {
        throw new Error(`deployment link escapes output: ${relative(root, path)}`);
      }
    } else if (entry.isDirectory()) checkLinks(path, root);
  }
}

export function verifyDeploy(source, target) {
  const frozen = new Map();
  for (const directory of packages(source)) {
    const pkg = JSON.parse(readFileSync(join(directory, "package.json"), "utf8"));
    const key = `${pkg.name}@${pkg.version}`;
    if (!frozen.has(key)) frozen.set(key, new Set());
    frozen.get(key).add(fingerprint(directory));
  }
  const workspaces = new Map(
    readdirSync(join(source, "packages")).map((name) => {
      const directory = join(source, "packages", name);
      const pkg = JSON.parse(readFileSync(join(directory, "package.json"), "utf8"));
      return [pkg.name, directory];
    })
  );
  const inventory = [];
  for (const directory of packages(target)) {
    const pkg = JSON.parse(readFileSync(join(directory, "package.json"), "utf8"));
    const key = `${pkg.name}@${pkg.version}`;
    if (workspaces.has(pkg.name)) {
      const original = join(workspaces.get(pkg.name), "dist");
      if (fingerprint(join(directory, "dist")) !== fingerprint(original)) {
        throw new Error(`workspace build differs from source: ${key}`);
      }
    } else {
      const sha256 = fingerprint(directory);
      if (!frozen.get(key)?.has(sha256)) {
        throw new Error(`package differs from frozen installation: ${key}`);
      }
      inventory.push({ name: pkg.name, version: pkg.version, sha256 });
    }
  }
  checkLinks(target);
  return inventory.toSorted((a, b) => `${a.name}@${a.version}`.localeCompare(`${b.name}@${b.version}`));
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.length !== 4) throw new Error("usage: verify-deploy.mjs source target");
  console.log(JSON.stringify(verifyDeploy(resolve(process.argv[2]), resolve(process.argv[3])), null, 2));
}
