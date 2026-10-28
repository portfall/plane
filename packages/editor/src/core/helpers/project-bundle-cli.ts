/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import {
  inspectProjectBundleDocument,
  inspectProjectBundleLegacyDocument,
  PROJECT_BUNDLE_DOCUMENT_FORMAT,
  PROJECT_BUNDLE_DOCUMENT_SUPPORT,
  PROJECT_BUNDLE_LEGACY_FORMAT,
  PROJECT_BUNDLE_MAX_BYTES,
  ProjectBundleDocumentError,
} from "./project-bundle";

const MAX_INPUT_BYTES = 16 * 1024 * 1024;
const MAX_OUTPUT_BYTES = 48 * 1024 * 1024;
const refusal = (code: string) => JSON.stringify({ format: PROJECT_BUNDLE_DOCUMENT_FORMAT, status: "refused", code });

async function main() {
  if (process.argv.length === 3 && process.argv[2] === "--help") {
    process.stdout.write(JSON.stringify({ format: PROJECT_BUNDLE_DOCUMENT_FORMAT, support: PROJECT_BUNDLE_DOCUMENT_SUPPORT, usage: "One JSON request on stdin; identity-only. No origin, actor, workspace or asset rewriting. Unknown references/history refuse.", max_document_bytes: PROJECT_BUNDLE_MAX_BYTES, max_input_bytes: MAX_INPUT_BYTES, max_output_bytes: MAX_OUTPUT_BYTES }) + "\n");
    return;
  }
  if (process.argv.length !== 2) throw new ProjectBundleDocumentError("INVALID_INPUT");
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const value of process.stdin) {
    const chunk = Buffer.from(value);
    size += chunk.length;
    if (size > MAX_INPUT_BYTES) throw new ProjectBundleDocumentError("LIMIT_EXCEEDED");
    chunks.push(chunk);
  }
  let request;
  try { request = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { throw new ProjectBundleDocumentError("INVALID_INPUT"); }
  const result = JSON.stringify(request?.format === PROJECT_BUNDLE_LEGACY_FORMAT
    ? await inspectProjectBundleLegacyDocument(request) : await inspectProjectBundleDocument(request));
  if (Buffer.byteLength(result) > MAX_OUTPUT_BYTES) throw new ProjectBundleDocumentError("LIMIT_EXCEEDED");
  process.stdout.write(result + "\n");
}

main().catch((error: unknown) => {
  process.stdout.write(refusal(error instanceof ProjectBundleDocumentError ? error.code : "INVALID_DOCUMENT") + "\n");
  process.exitCode = 2;
  process.stdin.destroy();
});
