/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import type { NextFunction, Request, Response } from "express";
import { describe, expect, it } from "vitest";

const enabled = process.env.PLANE_RUN_DJANGO_CONTENT_TESTS === "1";
const execute = promisify(execFile);
const SECRET = "synthetic-test-secret";
// The first module bootstraps the unique session DB before the PAT contracts.
const PYTHON_TESTS = "plane/tests/integration/test_pat_page_yjs.py plane/tests/contract/api/test_pages.py";
const PYTHON_COMMAND =
  "python -m pip install --quiet --no-cache-dir --no-compile --target /tmp/pat-pytest " +
  "pytest==9.0.3 pytest-django==4.12.0 pytest-mock==3.11.1 && " +
  `exec python -m pytest -q -s --tb=short -p no:cacheprovider -o addopts= --nomigrations --create-db ${PYTHON_TESTS}`;

const testServiceUrl = (name: string, protocols: string[]) => {
  const value = process.env[name];
  if (!value) throw new Error(`Opt-in integration requires ${name}.`);
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`Invalid synthetic test service URL: ${name}.`);
  }
  const octets = url.hostname.split(".").map(Number);
  const loopback = url.hostname === "127.0.0.1" || url.hostname === "[::1]";
  const dockerBridge =
    octets.length === 4 &&
    octets.every((octet) => Number.isInteger(octet) && octet >= 0 && octet <= 255) &&
    octets[0] === 172 &&
    octets[1] >= 16 &&
    octets[1] <= 31;
  const parentTestBridge =
    octets.length === 4 &&
    octets[0] === 192 &&
    octets[1] === 168 &&
    octets[2] === 144 &&
    Number.isInteger(octets[3]) &&
    octets[3] >= 0 &&
    octets[3] <= 255;
  if (!protocols.includes(url.protocol) || !(loopback || dockerBridge || parentTestBridge) || url.search || url.hash) {
    throw new Error(`Only local ephemeral test service URLs are allowed: ${name}.`);
  }
  if (
    url.protocol !== "redis:" &&
    (url.username !== "plane_test" || url.password !== "plane_test_only" || url.pathname !== "/plane_test")
  ) {
    throw new Error("Synthetic plane_test database credentials and name are required.");
  }
  if (url.protocol === "redis:" && (url.username || url.password || !["/", "/0"].includes(url.pathname))) {
    throw new Error("An unauthenticated ephemeral test cache is required.");
  }
  return value;
};

// Opt in only with parent-provided ephemeral Postgres/Valkey URLs. Ordinary test
// runs neither import the application environment nor launch any container.
describe.skipIf(!enabled)("Django PAT persistence with the real Live Yjs bridge", () => {
  it("keeps replacement history through warm publication, stale saves, and reopening", async () => {
    const databaseUrl = testServiceUrl("PLANE_TEST_DATABASE_URL", ["postgres:", "postgresql:"]);
    const redisUrl = testServiceUrl("PLANE_TEST_REDIS_URL", ["redis:"]);
    const apiSource = fileURLToPath(new URL("../../../api/", import.meta.url));
    if (existsSync(path.resolve(".env")) || existsSync(path.join(apiSource, ".env"))) {
      throw new Error("Use an env-file-free source checkout for this synthetic integration harness.");
    }

    const syntheticEnv = {
      API_BASE_URL: "http://127.0.0.1:9",
      LIVE_SERVER_SECRET_KEY: SECRET,
      LIVE_BASE_PATH: "/",
      REDIS_URL: redisUrl,
    };
    const previousEnv = new Map(Object.keys(syntheticEnv).map((key) => [key, process.env[key]]));
    Object.assign(process.env, syntheticEnv);

    const containerName = `plane-pat-yjs-test-${randomUUID()}`;
    let listener: Server | undefined;
    let hocuspocus: import("@hocuspocus/server").Hocuspocus | undefined;
    try {
      const [{ default: express }, { Hocuspocus }, Y, { z }, { registerController }, editor, controller] =
        await Promise.all([
          import("express"),
          import("@hocuspocus/server"),
          import("yjs"),
          import("zod"),
          import("@plane/decorators"),
          import("@plane/editor"),
          import("@/controllers/page-content.controller"),
        ]);
      // Match production's payload GC while retaining Yjs IDs/delete sets.
      // Database/Redis/TitleSync extensions are deliberately not installed here:
      // this harness proves the HTTP bridge, persistence and replay contracts.
      const server = new Hocuspocus({ quiet: true, yDocOptions: { gc: true, gcFilter: () => true } });
      hocuspocus = server;
      const app = express();
      app.use("/admin/documents", ...controller.pageContentMiddleware);
      registerController(app, controller.PageContentController, [server]);

      // Fixture operations exist only on this ephemeral test app. They use real
      // Hocuspocus document lifecycle and Yjs updates, without DB/network mocks.
      const fixtureSchema = z
        .object({
          action: z.enum(["load", "inspect", "replay", "reopen", "decode", "warm-edit", "unload"]),
          description_binary: z
            .string()
            .max(1024 * 1024)
            .optional(),
          warm_html: z
            .string()
            .max(16 * 1024)
            .optional(),
          warm_title: z.string().max(1024).optional(),
        })
        .strict();
      const fixture = async (req: Request, res: Response) => {
        try {
          const id = z.string().uuid().parse(req.params.documentId);
          const payload = fixtureSchema.parse(req.body);
          const inspect = (document: import("yjs").Doc) => {
            const content = editor.getAllDocumentFormatsFromDocumentEditorBinaryData(
              Y.encodeStateAsUpdate(document),
              true
            );
            const bodyIds: string[] = [];
            const visit = (parent: import("yjs").XmlFragment | import("yjs").XmlElement) => {
              for (const child of parent.toArray()) {
                if (child._item) bodyIds.push(`${child._item.id.client}:${child._item.id.clock}`);
                if (child instanceof Y.XmlElement) visit(child);
              }
            };
            visit(document.getXmlFragment("default"));
            return {
              description_binary: content.contentBinaryEncoded,
              description_html: content.contentHTML,
              description_json: content.contentJSON,
              title: content.titleHTML,
              body_ids: bodyIds,
            };
          };
          if (payload.action === "decode") {
            if (!payload.description_binary) return res.status(400).json({ message: "Fixture binary required." });
            const decoded = new Y.Doc();
            try {
              Y.applyUpdate(decoded, Buffer.from(payload.description_binary, "base64"));
              return res.json(inspect(decoded));
            } finally {
              decoded.destroy();
            }
          }
          let document = server.documents.get(id);
          if (payload.action === "unload") {
            if (document) await server.unloadDocument(document);
            return res.json({ status: "unloaded" });
          }
          if (payload.action === "reopen" && document) {
            await server.unloadDocument(document);
            document = undefined;
          }
          if (payload.action !== "inspect" && payload.action !== "warm-edit") {
            if (!payload.description_binary) return res.status(400).json({ message: "Fixture binary required." });
            if (!document && payload.action !== "replay") {
              document = await server.createDocument(id, { headers: {}, url: "/synthetic-pat-test" }, "test", {
                readOnly: false,
                requiresAuthentication: false,
                isAuthenticated: true,
              });
            }
            if (!document) return res.status(404).json({ message: "Fixture document not loaded." });
            Y.applyUpdate(document, Buffer.from(payload.description_binary, "base64"));
          }
          if (!document) return res.status(404).json({ message: "Fixture document not loaded." });
          if (payload.action === "warm-edit") {
            // A real unsaved edit in the loaded room, never a production seam.
            const parsed = new Y.Doc();
            try {
              Y.applyUpdate(
                parsed,
                editor.getBinaryDataFromDocumentEditorHTMLString(payload.warm_html ?? "", payload.warm_title)
              );
              const clonedChildren = (name: string) =>
                parsed
                  .getXmlFragment(name)
                  .toArray()
                  .map((child) => {
                    if (!(child instanceof Y.XmlElement) && !(child instanceof Y.XmlText)) {
                      throw new Error("Unexpected synthetic editor node.");
                    }
                    return child.clone();
                  });
              document.transact(() => {
                const body = document!.getXmlFragment("default");
                body.insert(body.length, clonedChildren("default"));
                if (payload.warm_title !== undefined) {
                  const title = document!.getXmlFragment("title");
                  title.delete(0, title.length);
                  title.insert(0, clonedChildren("title"));
                }
              });
            } finally {
              parsed.destroy();
            }
          }
          return res.json(inspect(document));
        } catch {
          return res.status(400).json({ message: "Synthetic fixture operation failed." });
        }
      };
      app.post(
        "/__pat-test/documents/:documentId",
        ...controller.pageContentMiddleware,
        (req: Request, res: Response, next: NextFunction) => {
          // eslint-disable-next-line promise/no-callback-in-promise -- Express 4 forwards rejected handlers through next.
          void fixture(req, res).catch(next);
        }
      );

      listener = app.listen(0, "127.0.0.1");
      await new Promise<void>((resolve, reject) => {
        listener!.once("listening", resolve);
        listener!.once("error", reject);
      });
      const origin = `http://127.0.0.1:${(listener.address() as AddressInfo).port}`;
      const environment = {
        PLANE_RUN_DJANGO_CONTENT_TESTS: "1",
        PLANE_TEST_DATABASE_URL: databaseUrl,
        PLANE_TEST_REDIS_URL: redisUrl,
        DATABASE_URL: databaseUrl,
        DATABASE_READ_REPLICA_URL: "",
        REDIS_URL: redisUrl,
        LIVE_BASE_URL: origin,
        LIVE_BASE_PATH: "/",
        LIVE_SERVER_SECRET_KEY: SECRET,
        DJANGO_SETTINGS_MODULE: "plane.settings.test",
        SECRET_KEY: "synthetic-pat-yjs-integration-django-key-for-isolated-tests",
        AMQP_URL: "memory://",
        CELERY_BROKER_URL: "memory://",
        PYTHONDONTWRITEBYTECODE: "1",
        PYTHONUNBUFFERED: "1",
        PYTHONPATH: "/tmp/pat-pytest:/code",
      };
      const args = [
        "run",
        "--rm",
        "--pull=never",
        "--name",
        containerName,
        "--network",
        "host",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--cpus=2",
        "--memory=2g",
        "--pids-limit=256",
        "--log-driver=none",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=512m",
        "--mount",
        `type=bind,src=${apiSource},dst=/code,readonly`,
        "--workdir",
        "/code",
        ...Object.entries(environment).flatMap(([key, value]) => ["--env", `${key}=${value}`]),
        "--entrypoint",
        "/bin/bash",
        "plane-ce-pages-api-test-api-tests:latest",
        "-lc",
        PYTHON_COMMAND,
      ];
      let containerFailure: string | undefined;
      try {
        const { stdout } = await execute("docker", args, {
          timeout: 240_000,
          maxBuffer: 2 * 1024 * 1024,
          killSignal: "SIGKILL",
        });
        expect(stdout.includes("PAT_YJS_RESULT: passed")).toBe(true);
        const count = stdout.match(/\b(\d+) passed\b/);
        expect(count).not.toBeNull();
        process.stdout.write(`PAT_YJS_PYTEST: ${count![1]} passed\n`);
      } catch (error) {
        // Never echo subprocess bodies, env values, or the command (which has
        // credentials). Only accept bounded ASCII metadata on known test paths.
        const failure = error as { stdout?: string; code?: string | number };
        const phases = [...(failure.stdout ?? "").matchAll(/PAT_YJS_PHASE: ([a-z_]+)/g)];
        const phase = phases.at(-1)?.[1] ?? "container_setup";
        const code = typeof failure.code === "number" ? failure.code : "failed";
        const safeFailures: string[] = [];
        const safeHttpStatuses: string[] = [];
        const metadataPattern =
          /^PAT_YJS_FAILURE: function=(test_[A-Za-z0-9_]{1,155}) path=(plane\/tests\/(?:integration\/test_pat_page_yjs|contract\/api\/test_pages)\.py) line=([1-9][0-9]{0,6}) type=([A-Za-z_][A-Za-z0-9_]{0,79})$/;
        for (const line of (failure.stdout ?? "").split("\n")) {
          if (safeFailures.length >= 40) break;
          if (line.length > 400) continue;
          const httpStatus = /^PAT_YJS_HTTP_STATUS: actual=([1-5][0-9]{2}) expected=([1-5][0-9]{2})$/.exec(line);
          if (httpStatus && safeHttpStatuses.length < 40) {
            const diagnostic = `actual=${httpStatus[1]} expected=${httpStatus[2]}`;
            if (!safeHttpStatuses.includes(diagnostic)) safeHttpStatuses.push(diagnostic);
            continue;
          }
          const metadata = metadataPattern.exec(line);
          if (!metadata || Number(metadata[3]) > 1_000_000) continue;
          const diagnostic = `${metadata[1]} ${metadata[2]}:${metadata[3]} ${metadata[4]}`;
          if (!safeFailures.includes(diagnostic)) safeFailures.push(diagnostic);
        }
        const diagnostics = safeFailures.length ? ` Failures: ${safeFailures.join("; ")}.` : "";
        const statuses = safeHttpStatuses.length ? ` HTTP statuses: ${safeHttpStatuses.join("; ")}.` : "";
        containerFailure = `Synthetic Django/Yjs regression failed in ${phase} (exit ${code}).${diagnostics}${statuses}`;
      } finally {
        // This exact UUID name belongs only to this run, including on timeout.
        await execute("docker", ["rm", "--force", containerName], { timeout: 10_000 }).catch(() => undefined);
      }
      if (containerFailure) throw new Error(containerFailure);
    } finally {
      try {
        if (listener) {
          listener.closeAllConnections();
          await promisify(listener.close.bind(listener))();
        }
        if (hocuspocus) {
          const server = hocuspocus;
          await Promise.all([...server.documents.values()].map((document) => server.unloadDocument(document)));
          await server.destroy();
        }
      } finally {
        for (const [key, value] of previousEnv) {
          if (value === undefined) delete process.env[key];
          else process.env[key] = value;
        }
      }
    }
  }, 270_000);
});
