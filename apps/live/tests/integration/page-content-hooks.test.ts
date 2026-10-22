/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { createServer } from "node:http";
import type { RequestListener } from "node:http";
import type { AddressInfo } from "node:net";
import { setTimeout as wait } from "node:timers/promises";
import { describe, it, vi } from "vitest";
import type { Hocuspocus } from "@hocuspocus/server";
import type RedisClient from "ioredis";

// Run this file alone in an env-file-free checkout. Default collection must not
// import the application, editor, Redis manager, or start any listener/client.
const enabled = process.env.PLANE_RUN_LIVE_HOOK_TESTS === "1";
type Content = ReturnType<typeof import("@/lib/page-content").preparePageContent>;
type RedisExtension = import("@/extensions/redis").Redis;

class HookCheck extends Error {}
class HookTimeout extends Error {}
const check: (condition: unknown) => asserts condition = (condition) => {
  if (!condition) throw new HookCheck();
};
const bounded = async <T>(operation: Promise<T>, milliseconds = 4000): Promise<T> => {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      operation,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new HookTimeout()), milliseconds);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
};
const delay = (milliseconds: number, signal: AbortSignal) => wait(milliseconds, undefined, { signal });
const until = async (condition: () => boolean, signal: AbortSignal) => {
  const deadline = Date.now() + 4000;
  // Poll a cross-process observation; concurrent waits cannot replace this loop.
  /* eslint-disable no-await-in-loop */
  while (!condition()) {
    if (Date.now() >= deadline) throw new HookTimeout();
    await delay(25, signal);
  }
  /* eslint-enable no-await-in-loop */
};

const testRedisUrl = () => {
  // No REDIS_URL fallback, DNS names, credentials, query options, or env files.
  const supplied = process.env.PLANE_TEST_REDIS_URL;
  check(supplied);
  let url: URL;
  try {
    url = new URL(supplied);
  } catch {
    throw new HookCheck();
  }
  const octets = url.hostname.split(".").map(Number);
  const ipv4 = octets.length === 4 && octets.every((part) => Number.isInteger(part) && part >= 0 && part <= 255);
  const local =
    url.hostname === "[::1]" ||
    (ipv4 &&
      (octets[0] === 127 ||
        (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
        (octets[0] === 192 && octets[1] === 168 && octets[2] === 144)));
  check(
    url.protocol === "redis:" &&
      local &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      ["", "/", "/0"].includes(url.pathname)
  );
  return url.href;
};

describe.skipIf(!enabled)("synthetic API with real production collaboration hooks", () => {
  it("publishes canonical history, fans out, and distinguishes publication from a cookie-context store", async () => {
    let phase = "guard";
    const servers: Hocuspocus[] = [];
    const redisExtensions: RedisExtension[] = [];
    const clients: RedisClient[] = [];
    const savedEnv = new Map<string, string | undefined>();
    const consoleSpies: { mockRestore: () => void }[] = [];
    let manager: typeof import("@/redis").redisManager | undefined;
    let restoreLogger: (() => void) | undefined;
    let disposeApi: (() => Promise<void>) | undefined;
    let failure: Error | undefined;
    let consoleErrors = 0;
    let loggerErrors = 0;
    let restoreErrorLog: (() => void) | undefined;
    let stopScenario = false;
    const lifetime = new AbortController();
    const pause = (milliseconds: number) => delay(milliseconds, lifetime.signal);
    const id = randomUUID();

    try {
      await bounded(
        (async () => {
          const redisUrl = testRedisUrl();
          // env.ts calls dotenv.config() for cwd/.env. Refuse it before importing.
          check(!existsSync(".env"));
          const workspaceSlug = `hooks-${randomUUID()}`;
          const projectId = randomUUID();
          const cookie = `sessionid=synthetic-${randomUUID()}`;
          const context = {
            workspaceSlug,
            projectId,
            cookie,
            documentType: "project_page" as const,
            userId: randomUUID(),
          };
          const pagePath = `/api/workspaces/${workspaceSlug}/projects/${projectId}/pages/${id}/`;
          let canonical: Content;
          let name = "Synthetic title";
          let fetches = 0;
          let details = 0;
          let invalidRequests = 0;
          const stores: Content[] = [];
          const titles: string[] = [];

          // This is deliberately a fake API, not Django. Metadata PATCH changes
          // only name; Python metadata-only-save semantics are tested elsewhere.
          const fakeApi: RequestListener = (req, res) => {
            req.setTimeout(1500, () => req.destroy());
            res.setTimeout(1500, () => res.destroy());
            void (async () => {
              check(req.headers.cookie === cookie);
              check(req.url === pagePath || req.url === `${pagePath}description/`);
              if (req.method === "GET" && req.url === `${pagePath}description/`) {
                fetches++;
                res.writeHead(200, { "content-type": "application/octet-stream" });
                res.end(Buffer.from(canonical.description_binary, "base64"));
                return;
              }
              if (req.method === "GET" && req.url === pagePath) {
                details++;
                res.writeHead(200, { "content-type": "application/json" });
                res.end(JSON.stringify({ name, description_html: "<p>WRONG HTML SEED</p>" }));
                return;
              }
              check(req.method === "PATCH");
              const chunks: Buffer[] = [];
              let size = 0;
              for await (const chunk of req) {
                const bytes = Buffer.from(chunk);
                size += bytes.length;
                check(size <= 64 * 1024);
                chunks.push(bytes);
              }
              const data = JSON.parse(Buffer.concat(chunks).toString()) as Record<string, unknown>;
              if (req.url === pagePath) {
                check(Object.keys(data).length === 1 && typeof data.name === "string");
                name = data.name;
                titles.push(name);
                check(titles.length <= 2); // Bound repeated callbacks, including unload.
              } else {
                check(
                  Object.keys(data).toSorted().join(",") === "description_binary,description_html,description_json" &&
                    typeof data.description_binary === "string" &&
                    typeof data.description_html === "string" &&
                    data.description_json !== null &&
                    typeof data.description_json === "object"
                );
                canonical = data as Content;
                stores.push(canonical);
              }
              res.writeHead(200, { "content-type": "application/json" });
              res.end(JSON.stringify({ name }));
            })().catch(() => {
              invalidRequests++;
              res.writeHead(400).end();
            });
          };
          let handler = fakeApi;
          const listener = createServer((req, res) => handler(req, res));
          disposeApi = async () => {
            listener.closeAllConnections();
            await new Promise<void>((resolve) => listener.close(() => resolve()));
          };
          phase = "api-listen";
          await bounded(
            new Promise<void>((resolve, reject) => {
              listener.once("error", () => reject(new HookCheck()));
              listener.listen(0, "127.0.0.1", resolve);
            })
          );
          const origin = `http://127.0.0.1:${(listener.address() as AddressInfo).port}`;
          const env = {
            API_BASE_URL: origin,
            REDIS_URL: redisUrl,
            REDIS_HOST: "",
            REDIS_PORT: "6379",
            LIVE_SERVER_SECRET_KEY: "synthetic-hook-secret",
            HOSTNAME: "synthetic-hooks",
            APP_VERSION: "test",
            PORT: "0",
            CORS_ALLOWED_ORIGINS: "",
            LIVE_BASE_PATH: "/live",
            COMPRESSION_LEVEL: "0",
            COMPRESSION_THRESHOLD: "5000",
            LOG_LEVEL: "error",
            DOTENV_KEY: "",
            HTTP_PROXY: "",
            HTTPS_PROXY: "",
            ALL_PROXY: "",
            http_proxy: "",
            https_proxy: "",
            all_proxy: "",
            NO_PROXY: "127.0.0.1",
            no_proxy: "127.0.0.1",
          };
          for (const [key, value] of Object.entries(env)) {
            savedEnv.set(key, process.env[key]);
            process.env[key] = value;
          }
          // Hooks can otherwise log raw Axios errors. Suppress arguments entirely;
          // assertions below report only a fixed phase and failure category.
          for (const method of ["log", "info", "warn", "debug", "error"] as const) {
            consoleSpies.push(
              vi.spyOn(console, method).mockImplementation(() => {
                if (method === "error") consoleErrors++;
              })
            );
          }
          phase = "imports";
          const { logger } = await import("@plane/logger");
          const wasSilent = logger.silent;
          logger.silent = true;
          restoreLogger = () => {
            logger.silent = wasSilent;
          };
          const errorLog = vi.spyOn(logger, "error").mockImplementation(() => {
            loggerErrors++;
            return logger;
          });
          restoreErrorLog = () => errorLog.mockRestore();
          const [
            { Hocuspocus },
            Y,
            editor,
            { getExtensions },
            redisModule,
            controller,
            { registerController },
            { default: express },
            { preparePageContent },
          ] = await Promise.all([
            import("@hocuspocus/server"),
            import("yjs"),
            import("@plane/editor"),
            import("@/extensions"),
            import("@/redis"),
            import("@/controllers/page-content.controller"),
            import("@plane/decorators"),
            import("express"),
            import("@/lib/page-content"),
          ]);
          lifetime.signal.throwIfAborted();
          manager = redisModule.redisManager;
          phase = "redis-ready";
          const initializing = manager.initialize();
          // Capture even a not-yet-connected manager client for failure cleanup.
          // No replacement client or mocked production extension is installed.
          const managerClient = Reflect.get(manager, "redisClient") as RedisClient | null;
          check(managerClient);
          clients.push(managerClient);
          managerClient.options.retryStrategy = () => null;
          await bounded(initializing);
          check(manager.isClientConnected());
          const old = editor.getBinaryDataFromDocumentEditorHTMLString("<p>OLD HOOK BODY</p>", name);
          const formats = editor.getAllDocumentFormatsFromDocumentEditorBinaryData(old, true);
          canonical = {
            description_binary: formats.contentBinaryEncoded,
            description_html: formats.contentHTML,
            description_json: formats.contentJSON,
          };
          const originalBinary = canonical.description_binary;

          const createInstance = async () => {
            lifetime.signal.throwIfAborted();
            const extensions = getExtensions();
            const redis = extensions.find(
              (extension): extension is RedisExtension => "pub" in extension && "sub" in extension
            );
            check(redis);
            redisExtensions.push(redis);
            clients.push(redis.pub as RedisClient, redis.sub as RedisClient);
            for (const client of [redis.pub, redis.sub])
              client.on("error", () => {
                loggerErrors++;
              });
            let ready!: () => void;
            const configured = new Promise<void>((resolve) => {
              ready = resolve;
            });
            const server = new Hocuspocus({
              quiet: true,
              extensions,
              debounce: 10000,
              yDocOptions: { gc: true, gcFilter: () => true },
              onConfigure: async () => {
                ready();
              },
            });
            servers.push(server);
            await bounded(configured); // Includes real Redis admin subscription.
            check(server.configuration.debounce === 10000);
            return server;
          };
          phase = "extensions-ready";
          const first = await createInstance();
          const second = await createInstance();
          const app = express();
          app.use("/admin/documents", ...controller.pageContentMiddleware);
          registerController(app, controller.PageContentController, [first]);
          app.use((req, res) => fakeApi(req, res));
          handler = app;
          const publish = async (content: Content) => {
            check(!stopScenario);
            const response = await fetch(`${origin}/admin/documents/${id}/publish-pat`, {
              method: "POST",
              headers: { "content-type": "application/json", "live-server-secret-key": env.LIVE_SERVER_SECRET_KEY },
              body: JSON.stringify({ description_binary: content.description_binary }),
              signal: AbortSignal.timeout(2000),
            });
            check(response.status === 200);
            check(((await response.json()) as { status: string }).status === "published");
          };
          const load = (server: Hocuspocus) => {
            lifetime.signal.throwIfAborted();
            return bounded(
              server.createDocument(
                id,
                { headers: { cookie }, url: "/synthetic-hooks" },
                "synthetic",
                {
                  readOnly: false,
                  requiresAuthentication: false,
                  isAuthenticated: true,
                },
                context
              )
            );
          };
          const visible = (document: import("yjs").Doc) =>
            editor.getAllDocumentFormatsFromDocumentEditorBinaryData(Y.encodeStateAsUpdate(document), true);
          phase = "canonical-fetch";
          const room = await load(first);
          const peer = await load(second);
          check(fetches === 2 && details === 0 && stores.length === 0);
          check(visible(room).contentHTML.includes("OLD HOOK BODY"));
          check(Buffer.from(Y.encodeStateAsUpdate(room)).equals(Buffer.from(originalBinary, "base64")));
          // afterLoadDocument awaits the real document subscription. Also require
          // Redis to report both actual subscribers before testing fanout.
          const subscribers = (await bounded(managerClient.pubsub("NUMSUB", `hocuspocus:${id}`))) as [string, number];
          check(Number(subscribers[1]) === 2);

          phase = "prepare-commit-publish";
          const prepared = preparePageContent({
            persistedBinary: Buffer.from(canonical.description_binary, "base64"),
            loadedDocument: room,
            previousHtml: "<p>WRONG HTML SEED</p>",
            title: name,
            replacementHtml: "<p>NEW HOOK BODY</p>",
          });
          check(visible(room).contentHTML.includes("OLD HOOK BODY"));
          canonical = prepared; // Synthetic durable commit must precede publish.
          let nullOrigin = false;
          const observeOrigin = (_update: Uint8Array, transactionOrigin: unknown) => {
            nullOrigin ||= transactionOrigin == null;
          };
          room.on("update", observeOrigin);
          try {
            await publish(prepared);
          } finally {
            room.off("update", observeOrigin);
          }
          check(nullOrigin && visible(room).contentHTML.includes("NEW HOOK BODY"));
          phase = "redis-fanout";
          await until(() => visible(peer).contentHTML.includes("NEW HOOK BODY"), lifetime.signal);
          check(!visible(peer).contentHTML.includes("OLD HOOK BODY"));

          phase = "title-publish";
          const titleOnly = preparePageContent({
            persistedBinary: Buffer.from(canonical.description_binary, "base64"),
            loadedDocument: room,
            previousHtml: canonical.description_html,
            title: "Published hook title",
            replaceTitle: true,
          });
          check(titleOnly.description_html === canonical.description_html);
          canonical = titleOnly;
          const publicationTime = Date.now();
          await publish(titleOnly);
          await until(() => visible(peer).titleHTML?.includes("Published hook title") === true, lifetime.signal);
          // Real title debounce is 5s. Both loaded rooms have real observers.
          await pause(5500);
          check(titles.length === 2 && titles.every((title) => title === "Published hook title"));
          const settledTitles = titles.length;
          phase = "publication-no-description-store";
          await pause(Math.max(0, publicationTime + 11000 - Date.now()));
          check(titles.length === settledTitles && stores.length === 0);
          check(canonical.description_binary === titleOnly.description_binary);

          phase = "unload-refetch-replay";
          await Promise.all(
            servers.map(async (server) => {
              const document = server.documents.get(id);
              check(document);
              await bounded(server.unloadDocument(document));
              check(!server.documents.has(id));
            })
          );
          // Hocuspocus unload alone does not invoke Redis.onDisconnect. Remove
          // exactly this fixture's subscriptions before reopening its documents.
          await Promise.all(redisExtensions.map((redis) => bounded(redis.sub.unsubscribe(`hocuspocus:${id}`))));
          const reopened = await load(first);
          check(Number(fetches) === 3 && details === 0);
          Y.applyUpdate(reopened, Buffer.from(originalBinary, "base64"));
          check(visible(reopened).contentHTML.includes("NEW HOOK BODY"));
          check(!visible(reopened).contentHTML.includes("OLD HOOK BODY"));
          check(visible(reopened).titleHTML?.includes("Published hook title"));

          phase = "connection-origin-store";
          const edit = preparePageContent({
            persistedBinary: Buffer.from(canonical.description_binary, "base64"),
            loadedDocument: reopened,
            previousHtml: canonical.description_html,
            title: "Published hook title",
            replacementHtml: "<p>CONNECTION HOOK BODY</p>",
          });
          const editTime = Date.now();
          // Connection-shaped Yjs origin exercises the real Hocuspocus hook path;
          // it does not claim to be a websocket or browser client.
          Y.applyUpdate(reopened, Buffer.from(edit.description_binary, "base64"), {
            context,
            socketId: "synthetic-connection",
          });
          await pause(500);
          check(stores.length === 0 && first.debouncer.isDebounced(`onStoreDocument-${id}`));
          await pause(Math.max(0, editTime + 11000 - Date.now()));
          await until(() => stores.length === 1, lifetime.signal);
          check(Date.now() - editTime >= 10000);
          const stored = stores[0];
          check(stored);
          const decoded = editor.getAllDocumentFormatsFromDocumentEditorBinaryData(
            Buffer.from(stored.description_binary, "base64"),
            true
          );
          check(
            decoded.contentHTML === stored.description_html &&
              JSON.stringify(decoded.contentJSON) === JSON.stringify(stored.description_json)
          );
          check(decoded.contentHTML.includes("CONNECTION HOOK BODY") && !decoded.contentHTML.includes("OLD HOOK BODY"));
          check(invalidRequests === 0 && consoleErrors === 0 && loggerErrors === 0 && titles.length === settledTitles);
        })(),
        48000
      );
    } catch (error) {
      const type = error instanceof HookTimeout ? "timeout" : error instanceof HookCheck ? "assertion" : "exception";
      failure = new Error(`Live hook integration failed: phase=${phase}; type=${type}`);
    } finally {
      stopScenario = true;
      lifetime.abort();
      // Always tear down real hooks and the manager, even after failed startup.
      // Each UUID is ours; never flush Redis, enumerate keys, or stop services.
      try {
        await bounded(
          (async () => {
            const results = await Promise.allSettled(
              servers.map(async (server) => {
                try {
                  await server.debouncer.executeNow(`onStoreDocument-${id}`);
                  await Promise.all(
                    Array.from(server.documents.values(), (document) => server.unloadDocument(document))
                  );
                } finally {
                  // Even a failed unload must reach real extension destruction.
                  for (const document of server.documents.values()) document.destroy();
                  server.documents.clear();
                  await server.destroy();
                }
              })
            );
            check(results.every((result) => result.status === "fulfilled"));
          })(),
          5000
        );
      } catch {
        failure ??= new Error("Live hook integration failed: phase=cleanup-hooks; type=exception");
      } finally {
        // Force-close all duplicated clients even if extension destruction hung.
        for (const client of clients) client.disconnect(false);
        for (const server of servers) for (const document of server.documents.values()) document.destroy();
        const results = await Promise.allSettled([
          manager ? bounded(manager.disconnect(), 1000) : Promise.resolve(),
          disposeApi ? bounded(disposeApi(), 1000) : Promise.resolve(),
        ]);
        if (results.some((result) => result.status === "rejected")) {
          failure ??= new Error("Live hook integration failed: phase=cleanup-clients; type=exception");
        }
        restoreErrorLog?.();
        restoreLogger?.();
        for (const spy of consoleSpies) spy.mockRestore();
        for (const [key, value] of savedEnv) {
          if (value === undefined) delete process.env[key];
          else process.env[key] = value;
        }
      }
    }
    if (failure) throw failure;
  }, 60000);
});
