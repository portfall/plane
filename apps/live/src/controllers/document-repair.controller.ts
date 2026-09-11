/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { createHash } from "node:crypto";
import type { Hocuspocus } from "@hocuspocus/server";
import type { Request, Response } from "express";
import * as Y from "yjs";
import { z } from "zod";
// plane imports
import { Controller, Middleware, Post } from "@plane/decorators";
import { getAllDocumentFormatsFromDocumentEditorBinaryData } from "@plane/editor";
import { logger } from "@plane/logger";
// lib
import { requireSecretKey } from "@/lib/auth-middleware";
// types
import type { HocusPocusServerContext } from "@/types";

const applyUpdateSchema = z.object({
  update_base64: z
    .string()
    .min(1)
    .max(1_500_000)
    .regex(/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/, "Invalid base64 update"),
  expected_current_html_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  expected_result_html_sha256: z.string().regex(/^[a-f0-9]{64}$/),
});

const documentIdSchema = z.string().uuid();

const getDocumentHtmlHash = (document: Y.Doc): string => {
  const { contentHTML } = getAllDocumentFormatsFromDocumentEditorBinaryData(Y.encodeStateAsUpdate(document), true);
  return createHash("sha256").update(contentHTML).digest("hex");
};

@Controller("/admin/documents")
export class DocumentRepairController {
  private readonly hocusPocusServer: Hocuspocus;

  constructor(hocusPocusServer: Hocuspocus) {
    this.hocusPocusServer = hocusPocusServer;
  }

  @Post("/:documentId/apply-update")
  @Middleware(requireSecretKey)
  async applyUpdate(req: Request, res: Response) {
    try {
      const documentId = documentIdSchema.parse(req.params.documentId);
      const payload = applyUpdateSchema.parse(req.body);
      const document = this.hocusPocusServer.documents.get(documentId);

      if (!document) {
        return res.status(409).json({
          message: "Document is not loaded on this Live instance",
        });
      }

      const connection = document.getConnections()[0];
      const context = connection?.context as HocusPocusServerContext | undefined;
      if (!context) {
        return res.status(409).json({
          message: "Document has no authenticated client context",
        });
      }

      const update = Buffer.from(payload.update_base64, "base64");
      const currentHtmlSha256 = getDocumentHtmlHash(document);
      if (currentHtmlSha256 !== payload.expected_current_html_sha256) {
        return res.status(409).json({
          message: "Loaded document does not match the expected state",
          current_html_sha256: currentHtmlSha256,
        });
      }

      // Validate the resulting document before mutating the connected document.
      // Yjs updates cannot be rolled back once broadcast to collaborative clients.
      const candidate = new Y.Doc();
      Y.applyUpdate(candidate, Y.encodeStateAsUpdate(document));
      Y.applyUpdate(candidate, update);
      const resultHtmlSha256 = getDocumentHtmlHash(candidate);
      candidate.destroy();

      if (resultHtmlSha256 !== payload.expected_result_html_sha256) {
        return res.status(409).json({
          message: "Candidate document does not match the expected repaired state",
          result_html_sha256: resultHtmlSha256,
        });
      }

      const directConnection = await this.hocusPocusServer.openDirectConnection(documentId, context);
      try {
        await directConnection.transact(
          (connectedDocument) => Y.applyUpdate(connectedDocument, update, "__hocuspocus__redis__origin__"),
          "admin-document-repair"
        );
      } finally {
        await directConnection.disconnect();
      }

      return res.status(200).json({
        status: "applied",
        document_id: documentId,
        connected_clients: document.getConnections().length,
        update_bytes: update.byteLength,
        current_html_sha256: currentHtmlSha256,
        result_html_sha256: resultHtmlSha256,
      });
    } catch (error) {
      if (error instanceof z.ZodError) {
        return res.status(400).json({
          message: "Validation error",
          context: {
            validationErrors: error.errors.map((validationError) => ({
              path: validationError.path.join("."),
              message: validationError.message,
            })),
          },
        });
      }

      logger.error("DOCUMENT_REPAIR_CONTROLLER: Failed to apply update", error);
      return res.status(500).json({
        message: "Internal server error.",
      });
    }
  }
}
