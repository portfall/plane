/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import * as Y from "yjs";
import {
  getAllDocumentFormatsFromDocumentEditorBinaryData,
  getBinaryDataFromDocumentEditorHTMLString,
} from "@plane/editor";

type PageContent = {
  description_binary: string;
  description_html: string;
  description_json: object;
};

type PageDocumentOptions = {
  persistedBinary: Uint8Array;
  loadedDocument?: Y.Doc;
  previousHtml: string;
  title: string;
};

type PreparePageContentOptions = PageDocumentOptions & {
  replacementHtml?: string;
  replaceTitle?: boolean;
};

type PreparePageAssetsOptions = PageDocumentOptions & {
  duplicatedAssets: { old_asset_id: string; new_asset_id: string }[];
};

type MergePageContentSnapshotOptions = {
  persistedBinary: Uint8Array;
  incomingBinary: Uint8Array;
};

export class PageContentSnapshotError extends Error {
  constructor() {
    super("Invalid page content snapshot.");
    this.name = "PageContentSnapshotError";
  }
}

const documentFormats = (document: Y.Doc): PageContent => {
  const { contentBinaryEncoded, contentHTML, contentJSON } = getAllDocumentFormatsFromDocumentEditorBinaryData(
    Y.encodeStateAsUpdate(document),
    false
  );
  return {
    description_binary: contentBinaryEncoded,
    description_html: contentHTML,
    description_json: contentJSON,
  };
};

const replaceFragment = (document: Y.Doc, parsed: Y.Doc, name: string) => {
  const fragment = document.getXmlFragment(name);
  // The ProseMirror parser emits elements/text, never Y.XmlHook nodes.
  const children = parsed.getXmlFragment(name).toArray() as (Y.XmlElement | Y.XmlText)[];
  fragment.delete(0, fragment.length);
  fragment.insert(
    0,
    children.map((child) => child.clone())
  );
};

const loadCanonicalHistory = (
  candidate: Y.Doc,
  { persistedBinary, loadedDocument, previousHtml, title }: PageDocumentOptions
) => {
  if (persistedBinary.byteLength) Y.applyUpdate(candidate, persistedBinary);
  if (loadedDocument) Y.applyUpdate(candidate, Y.encodeStateAsUpdate(loadedDocument));

  // An empty fragment can still have deletion history that must survive replays.
  const history = Y.decodeUpdate(Y.encodeStateAsUpdate(candidate));
  if (history.structs.length === 0 && history.ds.clients.size === 0) {
    Y.applyUpdate(candidate, getBinaryDataFromDocumentEditorHTMLString(previousHtml, title));
  }
};

export const preparePageContent = ({
  persistedBinary,
  loadedDocument,
  previousHtml,
  title,
  replacementHtml,
  replaceTitle = false,
}: PreparePageContentOptions): PageContent => {
  const candidate = new Y.Doc();
  const parsed = new Y.Doc();
  try {
    loadCanonicalHistory(candidate, { persistedBinary, loadedDocument, previousHtml, title });

    Y.applyUpdate(parsed, getBinaryDataFromDocumentEditorHTMLString(replacementHtml ?? "", title));
    candidate.transact(() => {
      if (replacementHtml !== undefined) replaceFragment(candidate, parsed, "default");
      if (replaceTitle || !candidate.share.has("title")) {
        if (candidate.getXmlFragment("title").toString() !== parsed.getXmlFragment("title").toString()) {
          replaceFragment(candidate, parsed, "title");
        }
      }
    });
    return documentFormats(candidate);
  } finally {
    parsed.destroy();
    candidate.destroy();
  }
};

export const preparePageAssets = ({ duplicatedAssets, ...source }: PreparePageAssetsOptions): PageContent => {
  const candidate = new Y.Doc();
  try {
    loadCanonicalHistory(candidate, source);
    const replacements = new Map(
      duplicatedAssets.map(({ old_asset_id, new_asset_id }) => [old_asset_id, new_asset_id])
    );
    candidate.transact(() => {
      for (const node of candidate.getXmlFragment("default").createTreeWalker(() => true)) {
        if (!(node instanceof Y.XmlElement) || node.nodeName !== "imageComponent") continue;
        const src = node.getAttribute("src");
        const replacement = src === undefined ? undefined : replacements.get(src);
        if (replacement !== undefined && replacement !== src) node.setAttribute("src", replacement);
      }
    });
    return documentFormats(candidate);
  } finally {
    candidate.destroy();
  }
};

export const mergePageContentSnapshot = ({
  persistedBinary,
  incomingBinary,
}: MergePageContentSnapshotOptions): PageContent => {
  const merged = new Y.Doc();
  try {
    const incoming = Y.decodeUpdate(incomingBinary);
    if (incoming.structs.length === 0 && incoming.ds.clients.size === 0) throw new PageContentSnapshotError();

    if (persistedBinary.byteLength) Y.applyUpdate(merged, persistedBinary);
    Y.applyUpdate(merged, incomingBinary);

    // Keep canonical tombstones even when a stale client omitted them.
    // Unseen concurrent insertions still follow ordinary Yjs merge semantics.
    return documentFormats(merged);
  } catch {
    throw new PageContentSnapshotError();
  } finally {
    merged.destroy();
  }
};
