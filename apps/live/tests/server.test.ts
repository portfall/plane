import type { AddressInfo } from "node:net";
import type { Server as HttpServer } from "node:http";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Server } from "@/server";

const synthetic = vi.hoisted(() => ({
  LIVE_SERVER_SECRET_KEY: "SYNTHETIC_HEADER_MUST_NOT_BE_LOGGED",
  LIVE_BASE_PATH: "/live",
  PORT: "0",
  CORS_ALLOWED_ORIGINS: "",
  COMPRESSION_LEVEL: 0,
  COMPRESSION_THRESHOLD: 5000,
}));
vi.mock("@/env", () => ({ env: synthetic }));

let listener: HttpServer | undefined;
afterEach(async () => {
  vi.restoreAllMocks();
  if (listener) {
    listener.closeAllConnections();
    await new Promise<void>((resolve, reject) =>
      listener!.close((error) => {
        if (error) {
          reject(error);
          return;
        }
        resolve();
      })
    );
    listener = undefined;
  }
});

async function listen(base: string) {
  synthetic.LIVE_BASE_PATH = base;
  const server = new Server() as unknown as {
    app: { listen(port: number, host: string): HttpServer };
    setupRoutes(hocuspocus: { documents: Map<string, unknown> }): void;
  };
  server.setupRoutes({ documents: new Map() });
  listener = server.app.listen(0, "127.0.0.1");
  await new Promise<void>((resolve) => listener!.once("listening", resolve));
  return `http://127.0.0.1:${(listener.address() as AddressInfo).port}${base.replace(/\/+$/, "")}/admin/documents/83d1fc7e-f22f-4310-9975-a24964785bd3/prepare-pat`;
}

describe("production private Page middleware", () => {
  it.each(["/live", "/live/", "/"])(
    "authenticates before parsing and accepts large trusted bodies at %s",
    async (base) => {
      const url = await listen(base);
      const unauthorized = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{malformed",
      });
      expect(unauthorized.status).toBe(401);
      const authorized = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json", "live-server-secret-key": synthetic.LIVE_SERVER_SECRET_KEY },
        body: JSON.stringify({
          persisted_binary: "",
          previous_html: "<p>OLD</p>",
          title: "Synthetic title",
          replacement_html: `<p>${"x".repeat(120000)}</p>`,
        }),
      });
      expect(authorized.status).toBe(200);
      await authorized.arrayBuffer();
    }
  );

  it("does not record administrative keys or cookies through the actual request logger", async () => {
    const captured: string[] = [];
    const outputStream = (console as unknown as { _stdout: typeof process.stdout })._stdout;
    vi.spyOn(outputStream, "write").mockImplementation(((value: string | Uint8Array) => {
      captured.push(value.toString());
      return true;
    }) as typeof process.stdout.write);
    const url = await listen("/live");
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "live-server-secret-key": synthetic.LIVE_SERVER_SECRET_KEY,
        cookie: "sessionid=SYNTHETIC_COOKIE_MUST_NOT_BE_LOGGED",
      },
      body: JSON.stringify({
        persisted_binary: "",
        previous_html: "<p>OLD</p>",
        title: "Synthetic title",
        replacement_html: "<p>NEW</p>",
      }),
    });
    expect(response.status).toBe(200);
    await response.arrayBuffer();
    await new Promise<void>((resolve) => setImmediate(resolve));
    const output = captured.join("");
    expect(output).toContain("POST");
    expect(output).not.toContain(synthetic.LIVE_SERVER_SECRET_KEY);
    expect(output).not.toContain("SYNTHETIC_COOKIE_MUST_NOT_BE_LOGGED");
    expect(output).not.toContain('"headers"');
  });
});
