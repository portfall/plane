/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { afterEach, describe, expect, it } from "vitest";
import * as Y from "yjs";
import {
  getAllDocumentFormatsFromDocumentEditorBinaryData,
  getBinaryDataFromDocumentEditorHTMLString,
} from "@plane/editor";
import {
  mergePageContentSnapshot,
  PageContentSnapshotError,
  preparePageAssets,
  preparePageContent,
} from "@/lib/page-content";

const NEW_HTML = '<p class="editor-paragraph-block">NEW body</p>';
const OLD_ASSET = "a11f084a-fb1d-47fb-b57c-b7f1232ea928";
const NEW_ASSET = "e522ef32-18ea-4527-8e0d-708bed6780e4";
const OTHER_ASSET = "96167bd4-6246-49b8-86e0-a6b54a14400e";
const documents: Y.Doc[] = [];

const documentFrom = (binary?: Uint8Array) => {
  const document = new Y.Doc({ gc: false });
  documents.push(document);
  if (binary?.byteLength) Y.applyUpdate(document, binary);
  return document;
};
const seed = (html: string, title = "Original title") => getBinaryDataFromDocumentEditorHTMLString(html, title);
const binaryFrom = (result: { description_binary: string }) =>
  new Uint8Array(Buffer.from(result.description_binary, "base64"));
const formats = (document: Y.Doc) =>
  getAllDocumentFormatsFromDocumentEditorBinaryData(Y.encodeStateAsUpdate(document), true);
const appendBody = (document: Y.Doc, html: string) => {
  const parsed = documentFrom(seed(html));
  const body = document.getXmlFragment("default");
  const children = parsed.getXmlFragment("default").toArray() as (Y.XmlElement | Y.XmlText)[];
  body.insert(
    body.length,
    children.map((child) => child.clone())
  );
};
const prepare = (persistedBinary: Uint8Array, replacementHtml = "<p>NEW body</p>") =>
  preparePageContent({
    persistedBinary,
    previousHtml: "<p>Fallback body</p>",
    title: "Fallback title",
    replacementHtml,
  });
const expectCoherent = (result: ReturnType<typeof preparePageContent>) => {
  const canonical = formats(documentFrom(binaryFrom(result)));
  expect(result.description_html).toBe(canonical.contentHTML);
  expect(result.description_json).toEqual(canonical.contentJSON);
};

afterEach(() => {
  for (const document of documents.splice(0)) document.destroy();
});

describe("preparePageContent", () => {
  it("removes replaced private UTF8 payloads from the canonical binary", () => {
    const privateText = "SYNTHETIC_PRIVATE_雪_body";
    const old = seed(`<p>${privateText}</p>`);
    const result = prepare(old);
    expect(Buffer.from(binaryFrom(result)).includes(Buffer.from(privateText))).toBe(false);
    const replayed = documentFrom(binaryFrom(result));
    for (const update of [old, binaryFrom(result), old, old]) Y.applyUpdate(replayed, update);
    expect(formats(replayed).contentHTML).toBe(NEW_HTML);
    expect(Buffer.from(Y.encodeStateAsUpdate(replayed)).includes(Buffer.from(privateText))).toBe(false);
  });

  it("negative control: merging an old snapshot with a fresh replacement seed keeps both bodies", () => {
    const old = seed("<p>OLD body</p>");
    const freshReplacement = documentFrom(seed("<p>NEW body</p>"));
    Y.applyUpdate(freshReplacement, old);
    expect(formats(freshReplacement).contentHTML).toContain("OLD body");
    expect(formats(freshReplacement).contentHTML).toContain("NEW body");
  });

  it("replaces in the existing history and survives repeated old snapshots in either order", () => {
    const original = seed("<p>OLD body</p>");
    const oldDocument = documentFrom(original);
    appendBody(oldDocument, "<p>OLD second block</p>");
    const old = Y.encodeStateAsUpdate(oldDocument);
    const result = prepare(old);
    expectCoherent(result);
    expect(result.description_html).toBe(NEW_HTML);
    for (const snapshots of [
      [original, old],
      [old, original],
    ]) {
      const replayed = documentFrom();
      for (const update of [...snapshots, binaryFrom(result), ...snapshots, ...snapshots])
        Y.applyUpdate(replayed, update);
      expect(formats(replayed).contentHTML).toBe(result.description_html);
      expect(Y.encodeStateVector(replayed)).toEqual(Y.encodeStateVector(documentFrom(binaryFrom(result))));
    }
  });

  it("includes and deletes both persisted-only and warm unsaved content without mutating the loaded doc", () => {
    const original = seed("<p>OLD body</p>");
    const persisted = documentFrom(original);
    const loaded = documentFrom(original);
    appendBody(persisted, "<p>Persisted only</p>");
    appendBody(loaded, "<p>Unsaved only</p>");
    const before = Y.encodeStateAsUpdate(loaded);
    const result = preparePageContent({
      persistedBinary: Y.encodeStateAsUpdate(persisted),
      loadedDocument: loaded,
      previousHtml: "<p>Fallback</p>",
      title: "Fallback title",
      replacementHtml: "<p>NEW body</p>",
    });
    expect(Y.encodeStateAsUpdate(loaded)).toEqual(before);
    const replayed = documentFrom(binaryFrom(result));
    Y.applyUpdate(replayed, before);
    Y.applyUpdate(replayed, Y.encodeStateAsUpdate(persisted));
    expect(formats(replayed).contentHTML).toBe(NEW_HTML);
    expectCoherent(result);
  });

  it.each(["empty bytes", "empty update"])("initializes a cold page without history (%s)", (encoding) => {
    const persistedBinary = encoding === "empty bytes" ? new Uint8Array() : Y.encodeStateAsUpdate(documentFrom());
    const result = prepare(persistedBinary);
    expect(result.description_html).toBe(NEW_HTML);
    expect(formats(documentFrom(binaryFrom(result))).titleHTML).toBe("Fallback title");
    expectCoherent(result);
  });

  it("preserves a deliberate empty body and its history rather than reseeding previous HTML", () => {
    const old = seed("<p>OLD body</p>");
    const empty = documentFrom(old);
    empty.getXmlFragment("default").delete(0, empty.getXmlFragment("default").length);
    const result = prepare(Y.encodeStateAsUpdate(empty));
    const replayed = documentFrom(binaryFrom(result));
    Y.applyUpdate(replayed, old);
    expect(formats(replayed).contentHTML).toBe(NEW_HTML);
    // Only one new client (the replacement transaction) should join the known history.
    expect(Y.decodeStateVector(Y.encodeStateVector(replayed)).size).toBe(
      Y.decodeStateVector(Y.encodeStateVector(empty)).size + 1
    );
  });

  it("retains unrelated shared types and their existing delete sets", () => {
    const persisted = documentFrom(seed("<p>OLD body</p>"));
    persisted.getMap("annotations").set("deleted", "Historical annotation");
    persisted.getMap("annotations").set("kept", "Kept annotation");
    const beforeDeletion = Y.encodeStateAsUpdate(persisted);
    persisted.getMap("annotations").delete("deleted");
    const result = prepare(Y.encodeStateAsUpdate(persisted));
    const replayed = documentFrom(binaryFrom(result));
    Y.applyUpdate(replayed, beforeDeletion);
    expect(replayed.getMap("annotations").toJSON()).toEqual({ kept: "Kept annotation" });
    expect(formats(replayed).contentHTML).toBe(NEW_HTML);
  });

  it.each(["Kept title", ""])(
    "preserves the existing title (%s) and replaces it only when explicitly requested",
    (title) => {
      const old = seed("<p>OLD body</p>", title);
      expect(formats(documentFrom(binaryFrom(prepare(old)))).titleHTML).toBe(title);
      const result = preparePageContent({
        persistedBinary: old,
        previousHtml: "",
        title: "New title",
        replacementHtml: "<p>NEW body</p>",
        replaceTitle: true,
      });
      const replayed = documentFrom(binaryFrom(result));
      Y.applyUpdate(replayed, old);
      expect(formats(replayed).titleHTML).toBe("New title");
      expect(formats(replayed).contentHTML).toBe(NEW_HTML);
    }
  );

  it("initializes a missing title without reseeding the existing body", () => {
    const old = getBinaryDataFromDocumentEditorHTMLString("<p>OLD body</p>");
    const result = prepare(old);
    expect(formats(documentFrom(binaryFrom(result))).titleHTML).toBe("Fallback title");
    expect(result.description_html).toBe(NEW_HTML);
  });

  it("preserves a title whose children were intentionally deleted", () => {
    const original = documentFrom(seed("<p>OLD body</p>", "Removed title"));
    const title = original.getXmlFragment("title");
    title.delete(0, title.length);
    const result = prepare(Y.encodeStateAsUpdate(original));
    expect(formats(documentFrom(binaryFrom(result))).titleHTML).toBe("");
  });

  it("supports an empty replacement body", () => {
    const result = prepare(seed("<p>OLD body</p>"), "");
    const replayed = documentFrom(binaryFrom(result));
    expect(result.description_html).not.toContain("OLD body");
    expect(formats(replayed).contentJSON).toMatchObject({ type: "doc", content: [{ type: "paragraph" }] });
    expectCoherent(result);
  });

  it("preserves the body and warm unsaved IDs when replacementHtml is omitted for a title-only edit", () => {
    const persisted = seed("<p>Kept body</p>", "Old title");
    const loaded = documentFrom(persisted);
    appendBody(loaded, "<p>Unsaved text</p>");
    loaded.getMap("annotations").set("kept", "Kept annotation");
    const before = Y.encodeStateAsUpdate(loaded);
    const bodyPosition = Y.createRelativePositionFromTypeIndex(loaded.getXmlFragment("default"), 0);
    const result = preparePageContent({
      persistedBinary: persisted,
      loadedDocument: loaded,
      previousHtml: "<p>Stale</p>",
      title: "New title",
      replaceTitle: true,
    });
    const saved = documentFrom(binaryFrom(result));
    expect(formats(saved).contentHTML).toBe(formats(loaded).contentHTML);
    expect(formats(saved).titleHTML).toBe("New title");
    expect(Y.createRelativePositionFromTypeIndex(saved.getXmlFragment("default"), 0)).toEqual(bodyPosition);
    expect(saved.getMap("annotations").toJSON()).toEqual({ kept: "Kept annotation" });
    expect(Y.encodeStateAsUpdate(loaded)).toEqual(before);
    expectCoherent(result);
  });

  it("does not edit an explicitly requested title that is already current, or an omitted body", () => {
    const persisted = seed("<p>Kept body</p>", "Same title");
    const result = preparePageContent({
      persistedBinary: persisted,
      previousHtml: "<p>Stale</p>",
      title: "Same title",
      replaceTitle: true,
    });
    expect(binaryFrom(result)).toEqual(persisted);
  });

  it("uses previous HTML on a cold title-only initialization", () => {
    const result = preparePageContent({
      persistedBinary: new Uint8Array(),
      previousHtml: "<p>Initial body</p>",
      title: "Initial title",
      replaceTitle: true,
    });
    const saved = documentFrom(binaryFrom(result));
    expect(formats(saved).contentHTML).toContain("Initial body");
    expect(formats(saved).titleHTML).toBe("Initial title");
  });

  it.each(["mermaid", "typescript"])("preserves %s code language through canonical formats", (language) => {
    const result = prepare(
      seed("<p>OLD body</p>"),
      `<pre><code class="language-${language}">graph TD\nA --&gt; B</code></pre>`
    );
    expect(result.description_html).toContain(`class="language-${language}"`);
    expect(result.description_json).toMatchObject({ content: [{ type: "codeBlock", attrs: { language } }] });
    expectCoherent(result);
  });

  it("does not resurrect earlier bodies when older replacement commits arrive late", () => {
    const original = seed("<p>OLD body</p>");
    const first = prepare(original, "<p>FIRST body</p>");
    const second = preparePageContent({
      persistedBinary: binaryFrom(first),
      previousHtml: first.description_html,
      title: "Fallback",
      replacementHtml: "<p>SECOND body</p>",
    });
    const replayed = documentFrom(binaryFrom(second));
    for (const update of [binaryFrom(first), original, binaryFrom(first)]) Y.applyUpdate(replayed, update);
    expect(formats(replayed).contentHTML).toBe('<p class="editor-paragraph-block">SECOND body</p>');
  });
});

describe("preparePageAssets", () => {
  it("changes only matching image src attrs, preserving nested IDs, unsaved text, title and other shared fields", () => {
    const persisted = seed(
      `<p>Kept text</p><blockquote><p>Nested text</p><image-component src="${OLD_ASSET}" alt="Kept alt" width="45%"></image-component></blockquote><image-component src="${OTHER_ASSET}"></image-component>`,
      "Kept title"
    );
    const loaded = documentFrom(persisted);
    appendBody(loaded, "<p>Unsaved text</p>");
    loaded.getMap("annotations").set("kept", "Kept annotation");
    const body = loaded.getXmlFragment("default");
    (body.get(0) as Y.XmlElement).setAttribute("src", OLD_ASSET);
    const images = [...body.createTreeWalker(() => true)].filter(
      (node): node is Y.XmlElement => node instanceof Y.XmlElement && node.nodeName === "imageComponent"
    );
    expect(images).toHaveLength(2);
    const attributes = images[0].getAttributes();
    const imagePosition = Y.createRelativePositionFromTypeIndex(images[0], 0);
    const bodyPosition = Y.createRelativePositionFromTypeIndex(body, 0);
    const titlePosition = Y.createRelativePositionFromTypeIndex(loaded.getXmlFragment("title"), 0);
    const before = Y.encodeStateAsUpdate(loaded);
    const result = preparePageAssets({
      persistedBinary: persisted,
      loadedDocument: loaded,
      previousHtml: "<p>Stale fallback</p>",
      title: "Fallback title",
      duplicatedAssets: [{ old_asset_id: OLD_ASSET, new_asset_id: NEW_ASSET }],
    });
    expect(Y.encodeStateAsUpdate(loaded)).toEqual(before);
    const saved = documentFrom(binaryFrom(result));
    const savedBody = saved.getXmlFragment("default");
    const savedImages = [...savedBody.createTreeWalker(() => true)].filter(
      (node): node is Y.XmlElement => node instanceof Y.XmlElement && node.nodeName === "imageComponent"
    );
    expect(savedImages[0].getAttributes()).toEqual({ ...attributes, src: NEW_ASSET });
    expect(savedImages[1].getAttribute("src")).toBe(OTHER_ASSET);
    expect((savedBody.get(0) as Y.XmlElement).getAttribute("src")).toBe(OLD_ASSET);
    expect(Y.createRelativePositionFromTypeIndex(savedImages[0], 0)).toEqual(imagePosition);
    expect(Y.createRelativePositionFromTypeIndex(savedBody, 0)).toEqual(bodyPosition);
    expect(Y.createRelativePositionFromTypeIndex(saved.getXmlFragment("title"), 0)).toEqual(titlePosition);
    expect(formats(saved).titleHTML).toBe("Kept title");
    expect(result.description_html).toContain("Kept text");
    expect(result.description_html).toContain("Unsaved text");
    expect(saved.getMap("annotations").toJSON()).toEqual({ kept: "Kept annotation" });
    expectCoherent(result);
    const repeated = preparePageAssets({
      persistedBinary: binaryFrom(result),
      previousHtml: "",
      title: "Ignored title",
      duplicatedAssets: [
        { old_asset_id: OLD_ASSET, new_asset_id: NEW_ASSET },
        { old_asset_id: OLD_ASSET, new_asset_id: NEW_ASSET },
      ],
    });
    expect(binaryFrom(repeated)).toEqual(binaryFrom(result));
  });

  it("seeds previous HTML and title only on a cold document without history", () => {
    const result = preparePageAssets({
      persistedBinary: new Uint8Array(),
      previousHtml: `<image-component src="${OLD_ASSET}"></image-component>`,
      title: "Cold title",
      duplicatedAssets: [{ old_asset_id: OLD_ASSET, new_asset_id: NEW_ASSET }],
    });
    expect(result.description_json).toMatchObject({ content: [{ type: "imageComponent", attrs: { src: NEW_ASSET } }] });
    expect(formats(documentFrom(binaryFrom(result))).titleHTML).toBe("Cold title");
    expectCoherent(result);
  });

  it("does not reseed an intentionally empty body or deleted title during an asset remap", () => {
    const empty = documentFrom(seed("<p>Removed</p>"));
    for (const name of ["default", "title"]) {
      const fragment = empty.getXmlFragment(name);
      fragment.delete(0, fragment.length);
    }
    const before = Y.encodeStateVector(empty);
    const result = preparePageAssets({
      persistedBinary: Y.encodeStateAsUpdate(empty),
      previousHtml: "<p>Stale fallback</p>",
      title: "Stale title",
      duplicatedAssets: [],
    });
    const saved = documentFrom(binaryFrom(result));
    expect(Y.encodeStateVector(saved)).toEqual(before);
    expect(saved.getXmlFragment("default").length).toBe(0);
    expect(saved.getXmlFragment("title").length).toBe(0);
  });
});

describe("mergePageContentSnapshot", () => {
  it("GCs deleted private UTF8 payloads during repeated and reordered stale snapshot merges", () => {
    const privateText = "SYNTHETIC_PRIVATE_雪_history";
    const old = seed(`<p>${privateText}</p>`);
    const server = documentFrom(old);
    server.getXmlFragment("default").delete(0, server.getXmlFragment("default").length);
    appendBody(server, "<p>NEW body</p>");
    const persisted = Y.encodeStateAsUpdate(server);
    expect(Buffer.from(persisted).includes(Buffer.from(privateText))).toBe(true);
    for (const updates of [
      [old, persisted, old],
      [persisted, old, persisted],
    ]) {
      let canonical = persisted;
      for (const incomingBinary of updates) {
        const result = mergePageContentSnapshot({ persistedBinary: canonical, incomingBinary });
        expect(result.description_html).toBe(NEW_HTML);
        canonical = binaryFrom(result);
        expect(Buffer.from(canonical).includes(Buffer.from(privateText))).toBe(false);
      }
    }
  });

  it("accepts full old snapshots repeatedly without resurrecting known deleted content", () => {
    const original = seed("<p>OLD body</p>");
    const current = prepare(original);
    let persistedBinary = binaryFrom(current);
    for (let attempt = 0; attempt < 3; attempt++) {
      const result = mergePageContentSnapshot({ persistedBinary, incomingBinary: original });
      expect(result.description_html).toBe(NEW_HTML);
      expectCoherent(result);
      persistedBinary = binaryFrom(result);
    }
  });

  it("merges current client edits while retaining canonical history omitted from the client snapshot", () => {
    const original = seed("<p>OLD body</p>");
    const canonical = prepare(original);
    // The older client does not carry the server's replacement or its tombstones.
    const incoming = documentFrom(original);
    appendBody(incoming, "<p>Client edit</p>");
    const result = mergePageContentSnapshot({
      persistedBinary: binaryFrom(canonical),
      incomingBinary: Y.encodeStateAsUpdate(incoming),
    });
    expect(result.description_html).toContain("NEW body");
    expect(result.description_html).toContain("Client edit");
    expect(result.description_html).not.toContain("OLD body");
    const replayed = documentFrom(binaryFrom(result));
    Y.applyUpdate(replayed, original);
    expect(formats(replayed).contentHTML).toBe(result.description_html);
    expectCoherent(result);
  });

  it("accepts edits by a client based on the current canonical snapshot", () => {
    const current = prepare(seed("<p>OLD body</p>"));
    const incoming = documentFrom(binaryFrom(current));
    appendBody(incoming, "<p>Current client edit</p>");
    const result = mergePageContentSnapshot({
      persistedBinary: binaryFrom(current),
      incomingBinary: Y.encodeStateAsUpdate(incoming),
    });
    expect(result.description_html).toBe(`${NEW_HTML}<p class="editor-paragraph-block">Current client edit</p>`);
    expectCoherent(result);
  });

  it("retains newer server deletions when a stale snapshot omits their tombstones", () => {
    const current = prepare(seed("<p>OLD body</p>"));
    const server = documentFrom(binaryFrom(current));
    appendBody(server, "<p>Later deleted on server</p>");
    const stale = Y.encodeStateAsUpdate(server);
    server.getXmlFragment("default").delete(1, 1);
    const result = mergePageContentSnapshot({ persistedBinary: Y.encodeStateAsUpdate(server), incomingBinary: stale });
    expect(result.description_html).toBe(NEW_HTML);
    const saved = documentFrom(binaryFrom(result));
    Y.applyUpdate(saved, stale);
    expect(formats(saved).contentHTML).toBe(NEW_HTML);
    expectCoherent(result);
  });

  it("rejects empty or corrupt incoming snapshots with a generic error", () => {
    const current = prepare(seed("<p>OLD body</p>"));
    const persistedBinary = binaryFrom(current);
    for (const incomingBinary of [
      new Uint8Array(),
      Y.encodeStateAsUpdate(documentFrom()),
      new Uint8Array([255]),
      new Uint8Array([0]),
    ]) {
      expect(() => mergePageContentSnapshot({ persistedBinary, incomingBinary })).toThrow(PageContentSnapshotError);
      expect(() => mergePageContentSnapshot({ persistedBinary, incomingBinary })).toThrow(
        "Invalid page content snapshot."
      );
    }
  });
});
