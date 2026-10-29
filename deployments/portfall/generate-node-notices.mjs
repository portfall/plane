import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { lstatSync, mkdirSync, readdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { basename, dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { fingerprint, packages, verifyDeploy } from "./verify-deploy.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const scopes = {
  web: "apps/web",
  admin: "apps/admin",
  space: "apps/space",
  live: "apps/live",
  "@plane/editor": "packages/editor",
};
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
const inside = (root, path) => path === root || path.startsWith(root + sep);
const noticeName =
  /(?:^|[._-])(?:licen[cs]es?|notices?|copying|copyrights?|readme|authors|contributors|attributions?)(?:$|[._-])/i;

function readBounded(root, path) {
  const stat = lstatSync(path);
  if (stat.isSymbolicLink()) throw new Error(`notice symlink refused: ${path}`);
  if (!stat.isFile() || !inside(root, realpathSync(path))) throw new Error(`notice outside package: ${path}`);
  if (stat.size > 1024 * 1024) throw new Error(`oversized notice: ${path}`);
  return readFileSync(path);
}

// Only package-owned regular files are read. Dependency links are never followed.
function noticeFiles(root) {
  const result = [];
  let visited = 0;
  let retainedBytes = 0;
  function visit(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true }).toSorted((a, b) =>
      a.name.localeCompare(b.name, "en")
    )) {
      if (++visited > 100000) throw new Error(`package traversal bound exceeded: ${root}`);
      if (["node_modules", ".git"].includes(entry.name)) continue;
      const path = join(directory, entry.name);
      if (entry.isDirectory()) visit(path);
      else if (noticeName.test(entry.name)) {
        const bytes = readBounded(root, path);
        retainedBytes += bytes.length;
        if (retainedBytes > 16 * 1024 * 1024) throw new Error(`package notice byte bound exceeded: ${root}`);
        result.push({ path: relative(root, path), bytes });
      }
    }
  }
  visit(root);
  return result;
}

export function generateNodeNotices(source, scope, report, deployment) {
  if (!Object.hasOwn(scopes, scope)) throw new Error(`unsupported notice scope: ${scope}`);
  const root = realpathSync(source);
  const store = realpathSync(join(root, "node_modules/.pnpm"));
  if (!inside(root, store)) throw new Error("installed store outside source root");
  if (deployment && !["live", "space"].includes(scope)) throw new Error("unsupported deployment notice scope");
  const deployedRoot = deployment ? realpathSync(deployment) : undefined;
  // Recompute from actual deployed bytes, never trust a supplied JSON inventory.
  const inventory = deployedRoot ? verifyDeploy(root, deployedRoot) : undefined;
  const data = JSON.parse(readFileSync(join(here, "node-notice-data.json"), "utf8"));
  const chunks = [
    Buffer.from(
      `THIRD_PARTY_NOTICES — Internal Plane (${scope})\nConservative installed production dependency closure, not exact bundled reachability.${deployedRoot ? " Plus every verified physical deployed package root." : ""}\nOriginal metadata declarations and source texts are retained below; no license approval is implied.\nQualified evidence gaps, if any, remain recorded in the manifest. Customer bundles excluded.\n\n`
    ),
  ];
  const manifest = {
    schema: 1,
    scope,
    closure: "pnpm 11.3.0 licenses list --prod; conservative installed superset",
    license_approved: false,
    packages: [],
    qualified_gaps: [],
  };
  let totalBytes = 0;
  function appendFile(label, bytes) {
    totalBytes += bytes.length;
    if (totalBytes > 128 * 1024 * 1024) throw new Error("notice bundle byte bound exceeded");
    const sha256 = hash(bytes);
    chunks.push(
      Buffer.from(`\n===== ${label} (${bytes.length} bytes; sha256 ${sha256}) =====\n`),
      bytes,
      Buffer.from("\n===== END ORIGINAL FILE =====\n")
    );
    return { sha256, bytes: bytes.length };
  }
  function addPackage(directory, expected, originalFiles, override, pathRoot = root) {
    const raw = readBounded(directory, join(directory, "package.json"));
    const metadata = JSON.parse(raw);
    if (metadata.name !== expected.name || !expected.versions.includes(metadata.version))
      throw new Error(`package identity/version mismatch: ${expected.name}`);
    const package_json_sha256 = hash(raw);
    const key = `${metadata.name}@${metadata.version}`;
    const pkg = {
      name: metadata.name,
      version: metadata.version,
      path: relative(pathRoot, directory),
      metadata,
      package_json_sha256,
      files: [],
    };
    appendFile(`${key}/package.json`, raw);
    if (override) {
      pkg.supplement = override;
      if (
        override.name !== metadata.name ||
        override.version !== metadata.version ||
        override.package_json_sha256 !== package_json_sha256 ||
        (metadata.gitHead && override.gitHead !== metadata.gitHead)
      )
        throw new Error(`notice override source/version mismatch: ${key}`);
      if (override.file) {
        const bytes = readBounded(here, join(here, override.file));
        if (hash(bytes) !== override.sha256) throw new Error(`notice override hash mismatch: ${key}`);
        originalFiles.push({ path: "UPSTREAM_LICENSE", bytes, source: override.source });
      } else {
        const gap = { package: key, source: override.source, reason: override.reason };
        if (!manifest.qualified_gaps.some((existing) => existing.package === key)) manifest.qualified_gaps.push(gap);
        chunks.push(
          Buffer.from(
            `\nQUALIFIED METADATA-ONLY NOTICE GAP: ${key}\n${override.reason}\nPrimary metadata: ${override.source}\n`
          )
        );
      }
    }
    if (!originalFiles.length && !override?.reason) throw new Error(`missing original notice source: ${key}`);
    if (originalFiles.length && originalFiles.every((file) => /^readme(?:$|[._-])/i.test(basename(file.path)))) {
      pkg.evidence_qualification =
        "README-only source evidence does not establish complete license text or license clearance.";
      chunks.push(Buffer.from(`\nEVIDENCE QUALIFICATION: ${key}\n${pkg.evidence_qualification}\n`));
    }
    for (const file of originalFiles)
      pkg.files.push({
        path: file.path,
        source: file.source ?? "installed package",
        ...appendFile(`${key}/${file.path}`, file.bytes),
      });
    manifest.packages.push(pkg);
    return metadata.version;
  }
  const planeFiles = [
    { path: "LICENSE.txt", source: "Plane root LICENSE.txt", bytes: readBounded(root, join(root, "LICENSE.txt")) },
  ];
  addPackage(
    root,
    { name: "plane", versions: [JSON.parse(readBounded(root, join(root, "package.json"))).version] },
    planeFiles
  );
  const workspace = join(root, scopes[scope]);
  const workspaceMetadata = JSON.parse(readBounded(workspace, join(workspace, "package.json")));
  addPackage(workspace, { name: scope, versions: [workspaceMetadata.version] }, planeFiles);
  const rows = Object.values(report).flat();
  if (!rows.length) throw new Error("empty production dependency closure");
  const seen = new Set();
  for (const row of rows.toSorted((a, b) => a.name.localeCompare(b.name, "en"))) {
    if (
      !row.name ||
      !Array.isArray(row.versions) ||
      !row.versions.length ||
      !Array.isArray(row.paths) ||
      !row.paths.length
    )
      throw new Error("invalid pnpm license row");
    const versions = new Set();
    for (const input of row.paths.toSorted()) {
      const directory = realpathSync(input);
      if (directory === store || !inside(store, directory))
        throw new Error(`package outside installed store: ${input}`);
      const raw = readBounded(directory, join(directory, "package.json"));
      const metadata = JSON.parse(raw);
      if (metadata.name !== row.name || !row.versions.includes(metadata.version))
        throw new Error(`package identity/version mismatch: ${row.name}`);
      versions.add(metadata.version);
      if (seen.has(directory)) continue;
      seen.add(directory);
      const originalFiles = noticeFiles(directory);
      addPackage(
        directory,
        row,
        originalFiles,
        originalFiles.length ? undefined : data.packages[`${row.name}@${metadata.version}`]
      );
    }
    if (row.versions.some((version) => !versions.has(version)))
      throw new Error(`missing installed source version: ${row.name}`);
  }
  if (deployedRoot) {
    const app = JSON.parse(readBounded(deployedRoot, join(deployedRoot, "package.json")));
    if (app.name !== scope || app.version !== workspaceMetadata.version)
      throw new Error("deployment app identity/version mismatch");
    const deployedStore = realpathSync(join(deployedRoot, "node_modules/.pnpm"));
    if (!inside(deployedRoot, deployedStore)) throw new Error("deployment store outside output");
    const workspaces = new Map(
      readdirSync(join(root, "packages")).map((name) => {
        const directory = join(root, "packages", name);
        const metadata = JSON.parse(readBounded(directory, join(directory, "package.json")));
        return [metadata.name, metadata.version];
      })
    );
    manifest.deployment = {
      packages_json_sha256: hash(JSON.stringify(inventory, null, 2) + "\n"),
      packages: [],
    };
    for (const input of packages(deployedRoot).toSorted()) {
      const directory = realpathSync(input);
      if (directory === deployedStore || !inside(deployedStore, directory))
        throw new Error(`package outside deployment store: ${input}`);
      const metadata = JSON.parse(readBounded(directory, join(directory, "package.json")));
      const owned = workspaces.has(metadata.name);
      if (owned && workspaces.get(metadata.name) !== metadata.version)
        throw new Error(`deployment workspace version mismatch: ${metadata.name}`);
      const originalFiles = noticeFiles(directory);
      if (owned) originalFiles.push(...planeFiles);
      const notice_package_index = manifest.packages.length;
      addPackage(
        directory,
        { name: metadata.name, versions: [metadata.version] },
        originalFiles,
        originalFiles.length ? undefined : data.packages[`${metadata.name}@${metadata.version}`],
        deployedRoot
      );
      manifest.packages[notice_package_index].origin = "verified deployment";
      manifest.deployment.packages.push({
        path: relative(deployedRoot, directory),
        name: metadata.name,
        version: metadata.version,
        sha256: fingerprint(directory),
        notice_package_index,
      });
    }
  }
  if (["live", "space", "@plane/editor"].includes(scope)) {
    if (process.version !== data.node.version)
      throw new Error(`Node version mismatch: expected ${data.node.version}, got ${process.version}`);
    const nodeLicense = readBounded(here, join(here, data.node.file));
    if (hash(nodeLicense) !== data.node.sha256 || nodeLicense.length !== data.node.bytes)
      throw new Error("Node original license hash/size mismatch");
    manifest.node = {
      version: data.node.version,
      source: data.node.source,
      ...appendFile(`Node.js ${process.version}/LICENSE`, nodeLicense),
    };
  }
  if (manifest.qualified_gaps.length)
    chunks.splice(
      1,
      0,
      Buffer.from(
        "UNRESOLVED SOURCE-NOTICE EVIDENCE:\n" +
          manifest.qualified_gaps.map((gap) => `${gap.package}: ${gap.reason}`).join("\n") +
          "\n\n"
      )
    );
  const notices = Buffer.concat(chunks);
  manifest.notices_sha256 = hash(notices);
  return { notices, manifest };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [source, scope, output, deployment] = process.argv.slice(2);
  if (
    ![5, 6].includes(process.argv.length) ||
    !Object.hasOwn(scopes, scope) ||
    ["live", "space"].includes(scope) !== Boolean(deployment)
  )
    throw new Error(
      "usage: generate-node-notices.mjs source web|admin|space|live|@plane/editor output [deployment-required-for-live-space]"
    );
  const root = realpathSync(source);
  const env = { ...process.env, COREPACK_ENABLE_NETWORK: "0", npm_config_offline: "true" };
  if (execFileSync("pnpm", ["--version"], { cwd: root, env, encoding: "utf8" }).trim() !== "11.3.0")
    throw new Error("notice input requires installed pnpm 11.3.0");
  const report = JSON.parse(
    execFileSync("pnpm", ["--filter", scope, "licenses", "list", "--prod", "--json"], {
      cwd: root,
      env,
      maxBuffer: 16 * 1024 * 1024,
    })
  );
  const { notices, manifest } = generateNodeNotices(root, scope, report, deployment);
  mkdirSync(output, { recursive: true });
  writeFileSync(join(output, "THIRD_PARTY_NOTICES"), notices);
  writeFileSync(join(output, "node-notices.json"), JSON.stringify(manifest, null, 2) + "\n");
  if (manifest.node) {
    const data = JSON.parse(readFileSync(join(here, "node-notice-data.json"), "utf8"));
    writeFileSync(join(output, "NODE_LICENSE"), readBounded(here, join(here, data.node.file)));
  }
}
