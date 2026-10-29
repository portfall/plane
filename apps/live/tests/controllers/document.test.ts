/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { Request, Response } from "express";
import { describe, expect, it, vi } from "vitest";
import { DocumentController } from "@/controllers/document.controller";

describe("DocumentController validation", () => {
  it("returns field errors for malformed conversion input", async () => {
    const response = { status: vi.fn(), json: vi.fn() };
    response.status.mockReturnValue(response);

    await new DocumentController().convertDocument(
      { body: { description_html: " ", variant: "invalid" } } as Request,
      response as unknown as Response
    );

    expect(response.status).toHaveBeenCalledWith(400);
    expect(response.json).toHaveBeenCalledWith({
      message: "Validation error",
      context: {
        validationErrors: expect.arrayContaining([
          { path: "description_html", message: expect.any(String) },
          { path: "variant", message: expect.any(String) },
        ]),
      },
    });
  });
});
