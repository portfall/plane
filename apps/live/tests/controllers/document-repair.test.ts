/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { createHash } from "node:crypto";
import { Document, type Hocuspocus } from "@hocuspocus/server";
import type { Request, Response } from "express";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as Y from "yjs";
import {
  getAllDocumentFormatsFromDocumentEditorBinaryData,
  getBinaryDataFromDocumentEditorHTMLString,
} from "@plane/editor";
import { DocumentRepairController } from "@/controllers/document-repair.controller";

const DOCUMENT_ID = "83d1fc7e-f22f-4310-9975-a24964785bd3";

const htmlHash = (document: Y.Doc) => {
  const { contentHTML } = getAllDocumentFormatsFromDocumentEditorBinaryData(Y.encodeStateAsUpdate(document), true);
  return createHash("sha256").update(contentHTML).digest("hex");
};

const makeDocument = (html: string) => {
  const document = new Document(DOCUMENT_ID);
  Y.applyUpdate(document, getBinaryDataFromDocumentEditorHTMLString(html));
  return document;
};

const makeRepairUpdate = (current: Y.Doc, repairedHtml: string) => {
  const repairDocument = new Y.Doc();
  Y.applyUpdate(repairDocument, Y.encodeStateAsUpdate(current));
  repairDocument.getXmlFragment("default").delete(0, repairDocument.getXmlFragment("default").length);
  Y.applyUpdate(repairDocument, getBinaryDataFromDocumentEditorHTMLString(repairedHtml));
  return Y.encodeStateAsUpdate(repairDocument);
};

const makeResponse = () => {
  const response = {
    status: vi.fn(),
    json: vi.fn(),
  };
  response.status.mockReturnValue(response);
  return response as unknown as Response;
};

const makeRequest = (body: object, documentId = DOCUMENT_ID) =>
  ({
    params: { documentId },
    body,
  }) as unknown as Request;

describe("DocumentRepairController", () => {
  let document: Document;
  let send: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    document = makeDocument("<p>Before repair</p>");
    send = vi.fn();
    vi.spyOn(document, "getConnections").mockReturnValue([
      {
        context: {
          cookie: "session-cookie",
          documentType: "project_page",
          projectId: "project-id",
          userId: "user-id",
          workspaceSlug: "workspace",
        },
        send,
      } as never,
    ]);
  });

  it("validates, broadcasts, and persists a repair against the connected document", async () => {
    const repairUpdate = makeRepairUpdate(document, '<pre><code class="language-mermaid">stateDiagram-v2</code></pre>');
    const candidate = new Y.Doc();
    Y.applyUpdate(candidate, Y.encodeStateAsUpdate(document));
    Y.applyUpdate(candidate, repairUpdate);

    const transact = vi.fn(async (callback: (connectedDocument: Y.Doc) => void) => callback(document));
    const disconnect = vi.fn(async () => undefined);
    const server = {
      documents: new Map([[DOCUMENT_ID, document]]),
      openDirectConnection: vi.fn(async () => ({ transact, disconnect })),
    } as unknown as Hocuspocus;
    const controller = new DocumentRepairController(server);
    const response = makeResponse();
    const expectedCurrentHash = htmlHash(document);
    const expectedResultHash = htmlHash(candidate);

    await controller.applyUpdate(
      makeRequest({
        update_base64: Buffer.from(repairUpdate).toString("base64"),
        expected_current_html_sha256: expectedCurrentHash,
        expected_result_html_sha256: expectedResultHash,
      }),
      response
    );

    expect(response.status).toHaveBeenCalledWith(200);
    expect(response.json).toHaveBeenCalledWith(
      expect.objectContaining({
        status: "applied",
        document_id: DOCUMENT_ID,
        current_html_sha256: expectedCurrentHash,
        result_html_sha256: expectedResultHash,
      })
    );
    expect(htmlHash(document)).toBe(expectedResultHash);
    expect(send).toHaveBeenCalled();
    expect(disconnect).toHaveBeenCalledOnce();
  });

  it("rejects a stale expected hash without mutating the document", async () => {
    const originalHash = htmlHash(document);
    const repairUpdate = makeRepairUpdate(document, "<p>After repair</p>");
    const server = {
      documents: new Map([[DOCUMENT_ID, document]]),
      openDirectConnection: vi.fn(),
    } as unknown as Hocuspocus;
    const response = makeResponse();

    await new DocumentRepairController(server).applyUpdate(
      makeRequest({
        update_base64: Buffer.from(repairUpdate).toString("base64"),
        expected_current_html_sha256: "0".repeat(64),
        expected_result_html_sha256: "1".repeat(64),
      }),
      response
    );

    expect(response.status).toHaveBeenCalledWith(409);
    expect(htmlHash(document)).toBe(originalHash);
    expect(server.openDirectConnection).not.toHaveBeenCalled();
  });

  it("rejects repairs when the document is not loaded", async () => {
    const server = {
      documents: new Map(),
      openDirectConnection: vi.fn(),
    } as unknown as Hocuspocus;
    const response = makeResponse();

    await new DocumentRepairController(server).applyUpdate(
      makeRequest({
        update_base64: "AA==",
        expected_current_html_sha256: "0".repeat(64),
        expected_result_html_sha256: "1".repeat(64),
      }),
      response
    );

    expect(response.status).toHaveBeenCalledWith(409);
    expect(server.openDirectConnection).not.toHaveBeenCalled();
  });

  it("rejects malformed repair payloads", async () => {
    const server = {
      documents: new Map([[DOCUMENT_ID, document]]),
      openDirectConnection: vi.fn(),
    } as unknown as Hocuspocus;
    const response = makeResponse();

    await new DocumentRepairController(server).applyUpdate(
      makeRequest({
        update_base64: "not base64",
        expected_current_html_sha256: "not-a-hash",
        expected_result_html_sha256: "also-not-a-hash",
      }),
      response
    );

    expect(response.status).toHaveBeenCalledWith(400);
    expect(server.openDirectConnection).not.toHaveBeenCalled();
  });
});
