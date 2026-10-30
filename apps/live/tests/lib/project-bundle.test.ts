/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import * as Y from "yjs";
import {
  getAllDocumentFormatsFromDocumentEditorBinaryData,
  getBinaryDataFromDocumentEditorHTMLString,
  getBinaryDataFromRichTextEditorHTMLString,
} from "@plane/editor";
import {
  inspectProjectBundleDocument,
  inspectProjectBundleLegacyDocument,
  ProjectBundleDocumentError,
  type ProjectBundleDocumentRequest,
} from "@plane/editor/lib";

const ASSET = "a11f084a-fb1d-47fb-b57c-b7f1232ea928";
const OTHER = "e522ef32-18ea-4527-8e0d-708bed6780e4";
const ACTOR = "701b6c6b-d985-4e91-a874-1e8d1ed1cdbf";
const SOURCE = "https://source.invalid";
const TARGET = "https://target.invalid";
const binary = (html: string) => getBinaryDataFromDocumentEditorHTMLString(html, "Exact title 雪");
const request = (bytes = binary("<p>Exact body</p>")): ProjectBundleDocumentRequest => ({
  format: "plane-project-document/1",
  variant: "document",
  binary: Buffer.from(bytes).toString("base64"),
  source_origin: SOURCE,
  target_origin: TARGET,
  references: [],
});

describe("project bundle legacy fields with exact native schemas", () => {
  const legacy = (html: string, json: object = {}) => ({
    format: "plane-project-legacy-document/1" as const,
    variant: "document" as const,
    html_base64: Buffer.from(html).toString("base64"),
    json_base64: Buffer.from(JSON.stringify(json)).toString("base64"),
    source_origin: SOURCE,
    target_origin: TARGET,
    references: [] as ProjectBundleDocumentRequest["references"],
  });
  it("keeps original HTML whitespace, JSON spelling and all bytes", async () => {
    const value = legacy(' \n<p>Exact legacy 雪</p>\n<pre><code class="language-mermaid">graph TD; A--&gt;B</code></pre>');
    const result = await inspectProjectBundleLegacyDocument(value);
    expect(result.html_base64).toBe(value.html_base64);
    expect(result.json_base64).toBe(value.json_base64);
    expect(result.html_sha256).toBe(createHash("sha256").update(Buffer.from(value.html_base64, "base64")).digest("hex"));
  });
  it("refuses tags, attributes and JSON keys that native parsing would otherwise discard", async () => {
    for (const html of ['<future-node src="opaque">Hidden</future-node>', '<p future="opaque">Text</p>']) {
      await expect(inspectProjectBundleLegacyDocument(legacy(html))).rejects.toThrow(ProjectBundleDocumentError);
    }
    await expect(inspectProjectBundleLegacyDocument(legacy("<p>Text</p>", { type: "doc", future: "opaque" }))).rejects.toThrow(ProjectBundleDocumentError);
  });
  it("maps only unchanged native image identities and refuses source links", async () => {
    const value = legacy(`<image-component src="${ASSET}"></image-component>`);
    value.references = [{ kind: "asset", source: ASSET, target: ASSET }];
    const result = await inspectProjectBundleLegacyDocument(value);
    expect(result.references).toContainEqual(expect.objectContaining({ kind: "asset", source: ASSET, target: ASSET }));
    await expect(inspectProjectBundleLegacyDocument(legacy(`<p><a href="${SOURCE}/private/">Source</a></p>`))).rejects.toThrow(ProjectBundleDocumentError);
  });
  it("keeps table cell colours but refuses a cell background that loads anything", async () => {
    const cell = (background: string) => legacy(`<table><tbody><tr><th background="${background}"><p>H</p></th></tr></tbody></table>`);
    for (const background of ["none", "#e5e7eb", "rgba(0, 0, 0, 0.1)", "var(--background-color-layer-1)"]) {
      const value = cell(background);
      expect((await inspectProjectBundleLegacyDocument(value)).html_base64).toBe(value.html_base64);
    }
    for (const background of ["https://external.invalid/x.png", "/uploads/x.png", "x.png", "url(x.png)"]) {
      await expect(inspectProjectBundleLegacyDocument(cell(background))).rejects.toThrow(ProjectBundleDocumentError);
    }
  });
  it("keeps presentational legacy styles but refuses escapes, loaders and script links", async () => {
    const value = legacy('<p style="text-align: center; color: #333">x</p>');
    expect((await inspectProjectBundleLegacyDocument(value)).html_base64).toBe(value.html_base64);
    for (const html of [
      '<p style="background: u\\72l(https://x.invalid/a.png)">x</p>',
      '<p style="background-image: image-set(\'x.png\' 1x)">x</p>',
      '<p style="position: fixed">x</p>',
      '<p><a href="javascript:alert(1)">x</a></p>',
      '<p><a href="evil.invalid/x">x</a></p>',
      '<p title="\\0075rl(x)">x</p>',
    ]) {
      await expect(inspectProjectBundleLegacyDocument(legacy(html))).rejects.toThrow(ProjectBundleDocumentError);
    }
  });
  it("retains external legacy links but refuses relative links and styled url() loads", async () => {
    const value = legacy('<p><a href="https://forge.example.invalid/org/repo/pulls/7">PR 7</a></p>');
    expect((await inspectProjectBundleLegacyDocument(value)).html_base64).toBe(value.html_base64);
    for (const html of ['<p><a href="/uploads/private-asset">Asset</a></p>', '<p><span style="background-image: url(https://external.invalid/x.png)">x</span></p>']) {
      await expect(inspectProjectBundleLegacyDocument(legacy(html))).rejects.toThrow(ProjectBundleDocumentError);
    }
  });
});
const inspect = (bytes: Uint8Array) => inspectProjectBundleDocument(request(bytes));
const retainDeleted = (html: string) => {
  const doc = new Y.Doc({ gc: false });
  Y.applyUpdate(doc, binary(html));
  const before = Y.encodeStateAsUpdate(doc);
  const body = doc.getXmlFragment("default");
  body.delete(0, body.length);
  const after = Y.encodeStateAsUpdate(doc);
  doc.destroy();
  return { before, after };
};
const refused = async (value: ProjectBundleDocumentRequest, code: string) => {
  await expect(inspectProjectBundleDocument(value)).rejects.toThrow(ProjectBundleDocumentError);
  try {
    await inspectProjectBundleDocument(value);
  } catch (error) {
    expect((error as ProjectBundleDocumentError).code).toBe(code);
  }
};

describe("project bundle identity-only offline codec", async () => {
  it("preserves original binary, retained payload and replay behavior without re-encoding", async () => {
    const { before, after } = retainDeleted("<p>Retained deleted history 雪</p>");
    const result = await inspect(after);
    expect(result.support).toBe("identity-literals/2");
    expect(result.binary).toBe(Buffer.from(after).toString("base64"));
    expect(result.before_sha256).toBe(createHash("sha256").update(after).digest("hex"));
    expect(result.after_sha256).toBe(result.before_sha256);
    expect(result.census.retained_structs).toBeGreaterThan(0);
    expect(Buffer.from(result.binary, "base64").includes(Buffer.from("Retained deleted history 雪"))).toBe(true);
    const reopened = new Y.Doc({ gc: false });
    Y.applyUpdate(reopened, Buffer.from(result.binary, "base64"));
    const projection = getAllDocumentFormatsFromDocumentEditorBinaryData(Y.encodeStateAsUpdate(reopened), true);
    Y.applyUpdate(reopened, before);
    Y.applyUpdate(reopened, after);
    expect(getAllDocumentFormatsFromDocumentEditorBinaryData(Y.encodeStateAsUpdate(reopened), true)).toEqual(projection);
    expect(Buffer.from(Y.encodeStateAsUpdate(reopened)).includes(Buffer.from("Retained deleted history 雪"))).toBe(true);
    reopened.destroy();
  });

  it("uses the native rich-text schema and preserves Mermaid code as literal text", async () => {
    const bytes = getBinaryDataFromRichTextEditorHTMLString('<pre><code class="language-mermaid">graph TD; A--&gt;B</code></pre>');
    const result = await inspectProjectBundleDocument({ ...request(bytes), variant: "rich" });
    expect(result.binary).toBe(Buffer.from(bytes).toString("base64"));
    expect(result.variant).toBe("rich");
  });

  it("censuses identity-mapped active and deleted native image references", async () => {
    for (const bytes of [binary(`<image-component src="${ASSET}"></image-component>`), retainDeleted(`<image-component src="${ASSET}"></image-component>`).after]) {
      const value = request(bytes);
      value.references = [{ kind: "asset", source: ASSET, target: ASSET }];
      const result = await inspectProjectBundleDocument(value);
      expect(result.references).toContainEqual(expect.objectContaining({ kind: "asset", source: ASSET, target: ASSET }));
      expect(result.binary).toBe(value.binary);
    }
  });

  it("refuses a changed asset UUID", async () => {
    const value = request(binary(`<image-component src="${ASSET}"></image-component>`));
    value.references = [{ kind: "asset", source: ASSET, target: OTHER }];
    await refused(value, "REFERENCE_REWRITE_REQUIRED");
  });

  it("refuses an unmapped retained asset even when current content is empty", async () => {
    await refused(request(retainDeleted(`<image-component src="${ASSET}"></image-component>`).after), "UNMAPPED_REFERENCE");
  });

  it("refuses active and retained authenticated source-origin links", async () => {
    const html = `<p><a href="${SOURCE}/workspace/pages/">Source Page</a></p>`;
    for (const bytes of [binary(html), retainDeleted(html).after]) await refused(request(bytes), "ORIGIN_REWRITE_REQUIRED");
  });

  it("retains external links, URLs and UUIDs in text as literals, byte for byte", async () => {
    const html = `<p>See <a href="https://forge.example.invalid/org/repo/pulls/7" target="_blank" rel="noopener">PR 7</a> and run 5f0c2a4e-1b2c-4d3e-8f90-a1b2c3d4e5f6 at https://ci.example.invalid/runs/1.</p><pre><code spellcheck="false">background: url(x.png)</code></pre>`;
    for (const bytes of [binary(html), retainDeleted(html).after]) {
      const value = request(bytes);
      const result = await inspectProjectBundleDocument(value);
      expect(result.binary).toBe(value.binary);
      expect(result.references).toEqual([]);
    }
    const value = request(binary(`<image-component src="${ASSET}" alt="https://external.invalid/screenshot"></image-component>`));
    value.references = [{ kind: "asset", source: ASSET, target: ASSET }];
    expect((await inspectProjectBundleDocument(value)).binary).toBe(value.binary);
  });

  it("refuses relative links and the source instance named by host alone", async () => {
    for (const href of ["/uploads/private-asset", "./private-asset", " /api/assets/x"]) {
      await refused(request(binary(`<p><a href="${href}">Asset</a></p>`)), "UNCLASSIFIED_REFERENCE");
    }
    await refused(request(binary(`<p>Moved from source.invalid/workspace/projects/x/issues today</p>`)), "ORIGIN_REWRITE_REQUIRED");
    await refused(request(binary(`<p><a href="HTTPS://SOURCE.INVALID/workspace/pages/">Source Page</a></p>`)), "ORIGIN_REWRITE_REQUIRED");
  });

  it("refuses changed user mentions and project/workspace embeds", async () => {
    const mention = request(binary(`<p><mention-component entity_identifier="${ACTOR}" entity_name="user"></mention-component></p>`));
    mention.references = [{ kind: "user", source: ACTOR, target: OTHER }];
    await refused(mention, "REFERENCE_REWRITE_REQUIRED");
    const embed = request(binary(`<issue-embed-component entity_identifier="${ACTOR}" entity_name="issue" workspace_identifier="source-workspace" project_identifier="${ASSET}"></issue-embed-component>`));
    embed.references = [{ kind: "issue", source: ACTOR, target: ACTOR }, { kind: "workspace", source: "source-workspace", target: "target-workspace" }, { kind: "project", source: ASSET, target: ASSET }];
    await refused(embed, "REFERENCE_REWRITE_REQUIRED");
  });

  it("refuses unknown attributes, marks, roots and subdocuments", async () => {
    for (const change of [
      (doc: Y.Doc) => (doc.getXmlFragment("default").get(0) as Y.XmlElement).setAttribute("future_attribute", "opaque"),
      (doc: Y.Doc) => ((doc.getXmlFragment("default").get(0) as Y.XmlElement).get(0) as Y.XmlText).format(0, 1, { future_mark: {} }),
      (doc: Y.Doc) => doc.getMap("future_root").set("key", "value"),
      (doc: Y.Doc) => doc.getMap("future_root").set("child", new Y.Doc()),
    ]) {
      const doc = new Y.Doc({ gc: false });
      Y.applyUpdate(doc, binary("<p>Body</p>"));
      change(doc);
      await expect(inspect(Y.encodeStateAsUpdate(doc))).rejects.toThrow(ProjectBundleDocumentError);
      doc.destroy();
    }
  });

  it("keeps the interactive editor's block ids and list tightness, and nothing like them elsewhere", async () => {
    const edited = (change: (body: Y.XmlFragment) => void) => {
      const doc = new Y.Doc({ gc: false });
      Y.applyUpdate(doc, binary("<p>Body</p><ul><li><p>Item</p></li></ul>"));
      change(doc.getXmlFragment("default"));
      const bytes = Y.encodeStateAsUpdate(doc);
      doc.destroy();
      return bytes;
    };
    const kept = edited((body) => {
      (body.get(0) as Y.XmlElement).setAttribute("id", OTHER);
      (body.get(1) as Y.XmlElement).setAttribute("id", ACTOR);
      (body.get(1) as Y.XmlElement).setAttribute("tight" as string, true as unknown as string);
    });
    const value = request(kept);
    const result = await inspectProjectBundleDocument(value);
    expect(result.binary).toBe(value.binary);
    expect(result.references).toEqual([]);
    for (const change of [
      (body: Y.XmlFragment) => (body.get(0) as Y.XmlElement).setAttribute("id", "https://source.invalid/x"),
      (body: Y.XmlFragment) => (body.get(0) as Y.XmlElement).setAttribute("tight" as string, true as unknown as string),
      (body: Y.XmlFragment) => (body.get(1) as Y.XmlElement).setAttribute("tight", "yes"),
    ]) {
      await expect(inspect(edited(change))).rejects.toThrow(ProjectBundleDocumentError);
    }
  });

  it("refuses script links and cell colours that would load anything", async () => {
    const linked = (href: string) => {
      const doc = new Y.Doc({ gc: false });
      Y.applyUpdate(doc, binary("<p>Body</p>"));
      ((doc.getXmlFragment("default").get(0) as Y.XmlElement).get(0) as Y.XmlText).format(0, 4, { link: { href } });
      const bytes = Y.encodeStateAsUpdate(doc);
      doc.destroy();
      return bytes;
    };
    expect((await inspect(linked("https://forge.example.invalid/org/repo"))).references).toEqual([]);
    for (const href of ["javascript:alert(1)", " JaVaScRiPt:alert(1)", "data:text/html,x", "\\\\evil.invalid/x", "evil.invalid/x"]) {
      await expect(inspect(linked(href))).rejects.toThrow(ProjectBundleDocumentError);
    }
    const table = (colour: string) => {
      const doc = new Y.Doc({ gc: false });
      Y.applyUpdate(doc, binary("<table><tbody><tr><td><p>C</p></td></tr></tbody></table>"));
      const cell = ((doc.getXmlFragment("default").get(0) as Y.XmlElement).get(0) as Y.XmlElement).get(0) as Y.XmlElement;
      cell.setAttribute("background", colour);
      const bytes = Y.encodeStateAsUpdate(doc);
      doc.destroy();
      return bytes;
    };
    expect((await inspect(table("#e5e7eb"))).references).toEqual([]);
    const coloured = (color: string) => {
      const doc = new Y.Doc({ gc: false });
      Y.applyUpdate(doc, binary("<p>Body</p>"));
      ((doc.getXmlFragment("default").get(0) as Y.XmlElement).get(0) as Y.XmlText).format(0, 4, { customColor: { color, backgroundColor: null } });
      const bytes = Y.encodeStateAsUpdate(doc);
      doc.destroy();
      return bytes;
    };
    expect((await inspect(coloured("var(--editor-colors-peach-text)"))).references).toEqual([]);
    for (const color of ["red; position: fixed; inset: 0", "red;background:red"]) {
      await expect(inspect(coloured(color))).rejects.toThrow(ProjectBundleDocumentError);
    }
    for (const colour of ["red; background-image: url(https://x.invalid/a.png)", "red; background-image: image-set(\"x.png\" 1x)", "u\\72l(x)"]) {
      await expect(inspect(table(colour))).rejects.toThrow(ProjectBundleDocumentError);
    }
  });

  it("refuses an incomplete update with unresolved predecessor structs", async () => {
    const doc = new Y.Doc({ gc: false });
    Y.applyUpdate(doc, binary("<p>Body</p>"));
    const vector = Y.encodeStateVector(doc);
    ((doc.getXmlFragment("default").get(0) as Y.XmlElement).get(0) as Y.XmlText).insert(1, "delta");
    await refused(request(Y.encodeStateAsUpdate(doc, vector)), "INCOMPLETE_HISTORY");
    doc.destroy();
  });

  it("refuses malformed, oversized or structurally unknown protocol input", async () => {
    await refused({ ...request(), binary: "!not-base64!" }, "INVALID_INPUT");
    await refused({ ...request(), binary: "A".repeat(14 * 1024 * 1024) }, "LIMIT_EXCEEDED");
    await refused({ ...request(), source_origin: "https://user:password@source.invalid" }, "INVALID_INPUT");
    await refused({ ...request(), future: true } as ProjectBundleDocumentRequest, "INVALID_INPUT");
  });

  it("the standalone CLI works with empty environment and emits bounded sanitized refusals", async () => {
    const cli = fileURLToPath(new URL("../../../../packages/editor/dist/project-bundle-codec.mjs", import.meta.url));
    const run = (input: string) => spawnSync(process.execPath, ["--max-old-space-size=256", cli], { input, encoding: "utf8", env: {}, timeout: 15000, maxBuffer: 48 * 1024 * 1024 });
    const accepted = run(JSON.stringify(request()));
    expect(accepted.status).toBe(0);
    expect(JSON.parse(accepted.stdout).support).toBe("identity-literals/2");
    expect(JSON.parse(accepted.stdout).schema_sha256).toBe((await inspectProjectBundleDocument(request())).schema_sha256);
    const rejected = run(JSON.stringify({ ...request(), binary: "SYNTHETIC_PRIVATE_NEVER_PRINT" }));
    expect(rejected.status).toBe(2);
    expect(JSON.parse(rejected.stdout)).toEqual({ format: "plane-project-document/1", status: "refused", code: "INVALID_INPUT" });
    expect(rejected.stderr).toBe("");
    expect(rejected.stdout).not.toContain("SYNTHETIC_PRIVATE_NEVER_PRINT");
  });
});
