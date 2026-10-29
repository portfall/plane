import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { generateNodeNotices } from "./generate-node-notices.mjs";

const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");

function fixture(run) {
  const root = mkdtempSync(join(tmpdir(), "plane-notices-"));
  const directory = join(root, "node_modules/.pnpm/example@1.2.3/node_modules/example");
  mkdirSync(directory, { recursive: true });
  mkdirSync(join(root, "apps/web"), { recursive: true });
  writeFileSync(join(root, "package.json"), '{"name":"plane","version":"1.4.2","license":"AGPL-3.0"}\n');
  writeFileSync(join(root, "apps/web/package.json"), '{"name":"web","version":"1.4.2"}\n');
  writeFileSync(join(root, "LICENSE.txt"), "Original Plane AGPL text and copyright\n");
  const metadata = '{\n "name":"example", "version":"1.2.3", "license":"MIT", "author":"Original author"\n}\n';
  writeFileSync(join(directory, "package.json"), metadata);
  writeFileSync(join(directory, "LICENSE"), "Original permission grant\r\nCopyright (c) Example\r\n");
  const report = { MIT: [{ name: "example", versions: ["1.2.3"], paths: [directory] }] };
  try {
    run({ root, directory, report, metadata });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

test("retains original Plane, metadata, nested license, NOTICE, README and attribution bytes with exact hashes", () => {
  fixture(({ root, directory, report, metadata }) => {
    mkdirSync(join(directory, "vendor/deep"), { recursive: true });
    const originals = {
      LICENSE: readFileSync(join(directory, "LICENSE")),
      "vendor/deep/NOTICE": Buffer.from("Third-party attribution\nCopyright Another author\n"),
      "vendor/deep/THIRD_PARTY_NOTICES": Buffer.from("Additional bundled attribution\nCopyright Bundled author\n"),
      "README.md": Buffer.from("Package documentation and attribution\n"),
      AUTHORS: Buffer.from("Original contributors\n"),
    };
    for (const [path, bytes] of Object.entries(originals)) writeFileSync(join(directory, path), bytes);
    const { notices, manifest } = generateNodeNotices(root, "web", report);
    assert.ok(notices.includes(Buffer.from(metadata)));
    assert.ok(notices.includes(readFileSync(join(root, "LICENSE.txt"))));
    const pkg = manifest.packages.find((p) => p.name === "example");
    assert.equal(pkg.version, "1.2.3");
    assert.equal(pkg.package_json_sha256, hash(metadata));
    assert.equal(pkg.metadata.author, "Original author");
    assert.equal(manifest.license_approved, false);
    assert.equal(manifest.notices_sha256, hash(notices));
    for (const [path, bytes] of Object.entries(originals)) {
      assert.ok(notices.includes(bytes), path);
      assert.equal(pkg.files.find((f) => f.path === path).sha256, hash(bytes));
    }
    assert.deepEqual(generateNodeNotices(root, "web", report), { notices, manifest });
  });
});

test("refuses missing source texts, wrong package identity or versions and empty closures", () => {
  fixture(({ root, directory, report }) => {
    report.MIT[0].versions = ["9.9.9"];
    assert.throws(() => generateNodeNotices(root, "web", report), /identity|version/);
    report.MIT[0].versions = ["1.2.3"];
    rmSync(join(directory, "LICENSE"));
    assert.throws(() => generateNodeNotices(root, "web", report), /missing.*notice/i);
    assert.throws(() => generateNodeNotices(root, "web", {}), /empty/);
    assert.throws(() => generateNodeNotices(root, "arbitrary", report), /scope/);
  });
});

test("refuses package traversal, escaping notice symlinks and oversized source files", () => {
  fixture(({ root, directory, report }) => {
    report.MIT[0].paths = [root];
    assert.throws(() => generateNodeNotices(root, "web", report), /outside.*store/);
    report.MIT[0].paths = [directory];
    rmSync(join(directory, "LICENSE"));
    symlinkSync(join(root, "LICENSE.txt"), join(directory, "LICENSE"));
    assert.throws(() => generateNodeNotices(root, "web", report), /symlink/);
    rmSync(join(directory, "LICENSE"));
    writeFileSync(join(directory, "LICENSE"), Buffer.alloc(1024 * 1024 + 1));
    assert.throws(() => generateNodeNotices(root, "web", report), /oversized/);
  });
});

test("retains the full pinned Node distribution license and refuses a different Node version", () => {
  fixture(({ root, report }) => {
    mkdirSync(join(root, "apps/live"), { recursive: true });
    writeFileSync(join(root, "apps/live/package.json"), '{"name":"live","version":"1.4.2"}\n');
    const descriptor = Object.getOwnPropertyDescriptor(process, "version");
    try {
      Object.defineProperty(process, "version", { value: "v22.23.4" });
      const { notices, manifest } = generateNodeNotices(root, "live", report);
      const original = readFileSync(new URL("./notice-data/node-v22.23.4-LICENSE", import.meta.url));
      assert.equal(original.length, 145485);
      assert.equal(manifest.node.sha256, "c738ae413cf561f174e34f6961f8ca458aae2369a73640dda6234c629b98bcc4");
      assert.ok(notices.includes(original));
      Object.defineProperty(process, "version", { value: "v0.0.0" });
      assert.throws(() => generateNodeNotices(root, "live", report), /Node version mismatch/);
    } finally {
      Object.defineProperty(process, "version", descriptor);
    }
  });
});

// Original package.json bytes from the frozen installation; no registry/network in tests.
const pinnedMetadata = {
  "client-only":
    '{\n  "name": "client-only",\n  "description": "This is a marker package to indicate that a module can only be used in Client Components.",\n  "keywords": [\n    "react"\n  ],\n  "version": "0.0.1",\n  "homepage": "https://reactjs.org/",\n  "bugs": "https://github.com/facebook/react/issues",\n  "license": "MIT",\n  "files": ["index.js", "error.js"],\n  "main": "index.js",\n  "exports": {\n    ".": {\n      "react-server": "./error.js",\n      "default": "./index.js"\n    }\n  }\n}\n',
  "@hocuspocus/provider":
    '{\n  "name": "@hocuspocus/provider",\n  "version": "2.15.2",\n  "description": "hocuspocus provider",\n  "homepage": "https://hocuspocus.dev",\n  "keywords": [\n    "hocuspocus",\n    "websocket",\n    "provider",\n    "yjs"\n  ],\n  "license": "MIT",\n  "type": "module",\n  "main": "dist/hocuspocus-provider.cjs",\n  "module": "dist/hocuspocus-provider.esm.js",\n  "types": "dist/packages/provider/src/index.d.ts",\n  "exports": {\n    "source": {\n      "import": "./src"\n    },\n    "default": {\n      "import": "./dist/hocuspocus-provider.esm.js",\n      "require": "./dist/hocuspocus-provider.cjs",\n      "types": "./dist/packages/provider/src/index.d.ts"\n    }\n  },\n  "files": [\n    "src",\n    "dist"\n  ],\n  "dependencies": {\n    "@hocuspocus/common": "^2.15.2",\n    "@lifeomic/attempt": "^3.0.2",\n    "lib0": "^0.2.87",\n    "ws": "^8.17.1"\n  },\n  "peerDependencies": {\n    "y-protocols": "^1.0.6",\n    "yjs": "^13.6.8"\n  },\n  "gitHead": "cd788b6a315f608ef531524409abdce1e6790726"\n}\n',
  "@tiptap/extension-emoji":
    '{\n  "name": "@tiptap/extension-emoji",\n  "description": "emoji extension for tiptap",\n  "version": "2.27.3",\n  "homepage": "https://tiptap.dev/api/nodes/emoji",\n  "keywords": [\n    "tiptap",\n    "tiptap extension"\n  ],\n  "license": "MIT",\n  "funding": {\n    "type": "github",\n    "url": "https://github.com/sponsors/ueberdosis"\n  },\n  "repository": {\n    "type": "git",\n    "url": "https://github.com/ueberdosis/tiptap",\n    "directory": "packages/extension-emoji"\n  },\n  "type": "module",\n  "exports": {\n    ".": {\n      "types": "./dist/index.d.ts",\n      "import": "./dist/index.js",\n      "require": "./dist/index.cjs"\n    }\n  },\n  "main": "dist/index.cjs",\n  "module": "dist/index.js",\n  "umd": "dist/index.umd.js",\n  "types": "dist/index.d.ts",\n  "files": [\n    "src",\n    "dist"\n  ],\n  "peerDependencies": {\n    "@tiptap/core": "^2.7.0",\n    "@tiptap/pm": "^2.7.0",\n    "@tiptap/suggestion": "^2.7.0"\n  },\n  "dependencies": {\n    "emoji-regex": "^10.4.0",\n    "emojibase-data": "^15",\n    "is-emoji-supported": "^0.0.5"\n  },\n  "devDependencies": {\n    "@tiptap/core": "^2.27.3",\n    "@tiptap/pm": "^2.27.3",\n    "@tiptap/suggestion": "^2.27.3",\n    "emoji-datasource": "^7.0.2",\n    "json5": "^2.2.3",\n    "tsm": "^2.3.0"\n  },\n  "scripts": {\n    "clean": "rm -rf dist",\n    "build": "npm run clean && rollup -c"\n  },\n  "publishConfig": {\n    "tag": "v2-latest"\n  }\n}\n',
};

test("uses version-bound primary originals and keeps the known metadata-only gap conspicuous", () => {
  for (const [name, original] of Object.entries(pinnedMetadata)) {
    fixture(({ root, directory, report }) => {
      const metadata = JSON.parse(original);
      rmSync(join(directory, "LICENSE"));
      writeFileSync(join(directory, "package.json"), original);
      report.MIT[0] = { name, versions: [metadata.version], paths: [directory] };
      const { notices, manifest } = generateNodeNotices(root, "web", report);
      const pkg = manifest.packages.find((p) => p.name === name);
      if (name === "client-only") {
        assert.equal(manifest.qualified_gaps[0].package, "client-only@0.0.1");
        assert.ok(notices.includes(Buffer.from("UNRESOLVED")));
        assert.equal(pkg.files.length, 0);
      } else {
        assert.equal(manifest.qualified_gaps.length, 0);
        assert.ok(pkg.files[0].source.includes(metadata.version === "2.15.2" ? "cd788b6a" : "0ed4a30a"));
        assert.ok(notices.includes(Buffer.from(name === "@hocuspocus/provider" ? "überdosis GbR" : "Tiptap GmbH")));
      }
      writeFileSync(join(directory, "package.json"), original + " ");
      assert.throws(() => generateNodeNotices(root, "web", report), /override source\/version mismatch/);
    });
  }
});
