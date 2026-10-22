/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { Hocuspocus } from "@hocuspocus/server";
import express, { type ErrorRequestHandler, type Request, type RequestHandler, type Response } from "express";
import * as Y from "yjs";
import { z } from "zod";
import { Controller, Middleware, Post } from "@plane/decorators";
import { logger } from "@plane/logger";
import { requireSecretKey } from "@/lib/auth-middleware";
import {
  mergePageContentSnapshot,
  PageContentSnapshotError,
  preparePageAssets,
  preparePageContent,
} from "@/lib/page-content";

const MAX_BYTES = 10 * 1024 * 1024;
const MAX_BASE64_LENGTH = 4 * Math.ceil(MAX_BYTES / 3);
const isBase64 = (value: string) => {
  if (value.length > MAX_BASE64_LENGTH || value.length % 4 !== 0) return false;
  const end = value.length - (value.endsWith("==") ? 2 : value.endsWith("=") ? 1 : 0);
  // A flat scan avoids the repeated-group regex stack limit on large snapshots.
  for (let index = 0; index < end; index++) {
    const code = value.charCodeAt(index);
    if (
      !(
        (code >= 65 && code <= 90) ||
        (code >= 97 && code <= 122) ||
        (code >= 48 && code <= 57) ||
        code === 43 ||
        code === 47
      )
    )
      return false;
  }
  return true;
};
const binarySchema = z
  .string()
  .max(MAX_BASE64_LENGTH)
  .refine(isBase64)
  .transform((value) => Buffer.from(value, "base64"))
  .refine((value) => value.byteLength <= MAX_BYTES);
const htmlSchema = z
  .string()
  .max(MAX_BYTES)
  .refine((value) => Buffer.byteLength(value) <= MAX_BYTES);
const prepareSchema = z
  .object({
    persisted_binary: binarySchema,
    previous_html: htmlSchema,
    title: htmlSchema,
    replacement_html: htmlSchema.optional(),
    replace_title: z.boolean().default(false),
    preserve_loaded_title: z.boolean().default(false),
  })
  .strict();
const mergeSchema = z.object({ persisted_binary: binarySchema, incoming_binary: binarySchema }).strict();
const assetsSchema = z
  .object({
    persisted_binary: binarySchema,
    previous_html: htmlSchema,
    title: htmlSchema,
    duplicated_assets: z
      .array(z.object({ old_asset_id: z.string().uuid(), new_asset_id: z.string().uuid() }).strict())
      .max(10000),
  })
  .strict();
const publishSchema = z.object({ description_binary: binarySchema }).strict();
const documentIdSchema = z.string().uuid();

// Authenticate before allocating the larger internal JSON body. The public parser
// keeps its existing limit; only this trusted service-to-service prefix uses it.
const rejectInvalidJson: ErrorRequestHandler = (error: unknown, _req, res, _next) => {
  const tooLarge = typeof error === "object" && error !== null && "type" in error && error.type === "entity.too.large";
  res.status(tooLarge ? 413 : 400).json({ message: "Invalid page content request." });
};
export const pageContentMiddleware: (RequestHandler | ErrorRequestHandler)[] = [
  requireSecretKey,
  express.json({ limit: "64mb" }),
  rejectInvalidJson,
];

const respondWithContent = (res: Response, content: ReturnType<typeof preparePageContent>) => {
  if (
    Buffer.from(content.description_binary, "base64").byteLength > MAX_BYTES ||
    Buffer.byteLength(content.description_html) > MAX_BYTES ||
    Buffer.byteLength(JSON.stringify(content.description_json)) > MAX_BYTES
  ) {
    return res.status(413).json({ message: "Page content exceeds the size limit." });
  }
  return res.status(200).json(content);
};

const failure = (res: Response, error: unknown) => {
  if (error instanceof z.ZodError || error instanceof PageContentSnapshotError) {
    return res.status(400).json({ message: "Invalid page content request." });
  }
  // Parser/Yjs errors can include document data. Never log supplied values.
  logger.error("PAGE_CONTENT: Internal content operation failed");
  return res.status(500).json({ message: "Page content operation failed." });
};

@Controller("/admin/documents")
export class PageContentController {
  constructor(private readonly server: Hocuspocus) {}

  @Post("/:documentId/prepare-pat")
  @Middleware(requireSecretKey)
  prepare(req: Request, res: Response) {
    try {
      const id = documentIdSchema.parse(req.params.documentId);
      const payload = prepareSchema.parse(req.body);
      const loadedDocument = this.server.documents.get(id);
      // Hocuspocus registers a room before onLoadDocument has finished. Only a
      // ready room with a known title can own the app callback's warm title.
      const preserveLoadedTitle =
        payload.preserve_loaded_title &&
        loadedDocument &&
        !loadedDocument.isLoading &&
        loadedDocument.share.has("title");
      const content = preparePageContent({
        persistedBinary: payload.persisted_binary,
        loadedDocument,
        previousHtml: payload.previous_html,
        title: payload.title,
        replacementHtml: payload.replacement_html,
        replaceTitle: payload.replace_title && !preserveLoadedTitle,
      });
      return respondWithContent(res, content);
    } catch (error) {
      return failure(res, error);
    }
  }

  @Post("/:documentId/prepare-assets")
  @Middleware(requireSecretKey)
  prepareAssets(req: Request, res: Response) {
    try {
      const id = documentIdSchema.parse(req.params.documentId);
      const payload = assetsSchema.parse(req.body);
      return respondWithContent(
        res,
        preparePageAssets({
          persistedBinary: payload.persisted_binary,
          loadedDocument: this.server.documents.get(id),
          previousHtml: payload.previous_html,
          title: payload.title,
          duplicatedAssets: payload.duplicated_assets,
        })
      );
    } catch (error) {
      return failure(res, error);
    }
  }

  @Post("/:documentId/merge-snapshot")
  @Middleware(requireSecretKey)
  merge(req: Request, res: Response) {
    try {
      documentIdSchema.parse(req.params.documentId);
      const payload = mergeSchema.parse(req.body);
      return respondWithContent(
        res,
        mergePageContentSnapshot({
          persistedBinary: payload.persisted_binary,
          incomingBinary: payload.incoming_binary,
        })
      );
    } catch (error) {
      return failure(res, error);
    }
  }

  @Post("/:documentId/publish-pat")
  @Middleware(requireSecretKey)
  publish(req: Request, res: Response) {
    try {
      const id = documentIdSchema.parse(req.params.documentId);
      const payload = publishSchema.parse(req.body);
      mergePageContentSnapshot({ persistedBinary: new Uint8Array(), incomingBinary: payload.description_binary });
      const document = this.server.documents.get(id);
      if (!document) return res.status(200).json({ status: "not_loaded" });

      // The caller has already committed this exact update under the Page row
      // lock. No await separates lookup and mutation. An undefined origin lets
      // Redis/local clients receive it without scheduling a cookie-context save.
      Y.applyUpdate(document, payload.description_binary);
      return res.status(200).json({ status: "published" });
    } catch (error) {
      return failure(res, error);
    }
  }
}
