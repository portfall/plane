/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { createRequire } from "node:module";
import { Node, mergeAttributes } from "@tiptap/core";
import { generateHTML } from "@tiptap/html";
import { describe, expect, it } from "vitest";
import { z } from "zod";

// Resolve the remaining deepmerge consumer through its real dependency chain.
const uiRequire = createRequire(new URL("../../../../packages/ui/package.json", import.meta.url));
const storybookRequire = createRequire(uiRequire.resolve("@storybook/react-webpack5"));
const builderRequire = createRequire(storybookRequire.resolve("@storybook/builder-webpack5"));
const checkerRequire = createRequire(builderRequire.resolve("fork-ts-checker-webpack-plugin"));
const deepmerge = checkerRequire("deepmerge") as (
  target: Record<string, unknown>,
  source: Record<string, unknown>
) => Record<string, unknown>;

describe("dependency security regressions", () => {
  it("does not turn JSON-origin __proto__ into inherited DOM attributes", () => {
    const input = JSON.parse('{"__proto__":{"src":"invalid://canary","onerror":"alert(1)"}}');
    const attributes = mergeAttributes({ class: "first" }, input);
    expect.soft(Object.getPrototypeOf(attributes)).toBe(Object.prototype);
    expect.soft(attributes.src).toBeUndefined();
    expect.soft(attributes.onerror).toBeUndefined();
    expect(attributes.class).toBe("first");
    expect(mergeAttributes({ class: "first" }, { class: "second" }).class).toBe("first second");

    const html = generateHTML({ type: "doc", content: [{ type: "image" }] }, [
      Node.create({ name: "doc", topNode: true, content: "image" }),
      Node.create({ name: "text", group: "inline" }),
      Node.create({ name: "image", renderHTML: () => ["img", attributes] }),
    ]);
    expect(html).toContain("<img");
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("invalid://canary");
  });

  it("rejects oversized constrained arrays before traversing their elements", () => {
    let visited = 0;
    const item = z.string().refine(() => {
      visited++;
      return false;
    });
    const result = z.array(item).max(2).safeParse(Array(1000).fill("invalid"));
    expect(result.success).toBe(false);
    expect(visited).toBe(0);
    if (!result.success) expect(result.error.issues).toHaveLength(1);
    expect(z.array(z.string()).max(2).parse(["valid"])).toEqual(["valid"]);
  });

  it("does not copy a target's own __proto__ into the result's prototype", () => {
    const result = deepmerge(JSON.parse('{"__proto__":{"inheritedCanary":true},"safe":1}'), {});
    expect(Object.getPrototypeOf(result)).toBe(Object.prototype);
    expect(result.inheritedCanary).toBeUndefined();
    expect(result.safe).toBe(1);
  });

  it("does not poison a result when the merge target has no prototype", () => {
    const result = deepmerge(Object.create(null), JSON.parse('{"__proto__":{"inheritedCanary":true},"safe":1}'));
    expect(Object.getPrototypeOf(result)).toBe(Object.prototype);
    expect(result.inheritedCanary).toBeUndefined();
    expect(result.safe).toBe(1);
    expect(deepmerge({ nested: { a: 1 }, list: [1] }, { nested: { b: 2 }, list: [2] })).toEqual({
      nested: { a: 1, b: 2 },
      list: [1, 2],
    });
  });
});
