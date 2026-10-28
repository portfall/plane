import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { verifyDeploy } from "./verify-deploy.mjs";

test("production deployment preserves frozen bytes and refuses changed packages or escaping links", () => {
  const task = mkdtempSync(join(tmpdir(), "plane-deploy-test-"));
  try {
    const source = join(task, "source");
    const target = join(task, "target");
    const relative = "node_modules/.pnpm/example@1.0.0/node_modules/example";
    for (const root of [source, target]) {
      mkdirSync(join(root, relative), { recursive: true });
      writeFileSync(join(root, relative, "package.json"), JSON.stringify({ name: "example", version: "1.0.0" }));
      writeFileSync(join(root, relative, "index.js"), "export default 42;");
    }
    mkdirSync(join(source, "packages"));
    assert.equal(verifyDeploy(source, target)[0].version, "1.0.0");
    writeFileSync(join(target, relative, "index.js"), "export default 43;");
    assert.throws(() => verifyDeploy(source, target), /differs from frozen/);
    writeFileSync(join(target, relative, "index.js"), "export default 42;");
    writeFileSync(join(target, relative, "package.json"), JSON.stringify({ name: "example", version: "2.0.0" }));
    assert.throws(() => verifyDeploy(source, target), /example@2.0.0/);
    writeFileSync(join(target, relative, "package.json"), JSON.stringify({ name: "example", version: "1.0.0" }));
    symlinkSync(source, join(target, "outside"));
    assert.throws(() => verifyDeploy(source, target), /escapes output/);
  } finally {
    rmSync(task, { recursive: true, force: true });
  }
});

test("file boundaries cannot hide a changed package", () => {
  const task = mkdtempSync(join(tmpdir(), "plane-deploy-boundaries-"));
  try {
    const source = join(task, "source");
    const target = join(task, "target");
    const relative = "node_modules/.pnpm/example@1.0.0/node_modules/example";
    for (const root of [source, target]) {
      mkdirSync(join(root, relative), { recursive: true });
      writeFileSync(join(root, relative, "package.json"), JSON.stringify({ name: "example", version: "1.0.0" }));
    }
    mkdirSync(join(source, "packages"));
    writeFileSync(join(source, relative, "a"), "one\0b\0two");
    writeFileSync(join(target, relative, "a"), "one");
    writeFileSync(join(target, relative, "b"), "two");
    assert.throws(() => verifyDeploy(source, target), /differs from frozen/);
  } finally {
    rmSync(task, { recursive: true, force: true });
  }
});
