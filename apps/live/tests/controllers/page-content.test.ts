import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { Document, Hocuspocus } from "@hocuspocus/server";
import express from "express";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as Y from "yjs";
import { registerController } from "@plane/decorators";
import {
  getAllDocumentFormatsFromDocumentEditorBinaryData,
  getBinaryDataFromDocumentEditorHTMLString,
} from "@plane/editor";
import { PageContentController, pageContentMiddleware } from "@/controllers/page-content.controller";
import { mergePageContentSnapshot } from "@/lib/page-content";

vi.mock("@/env", () => ({ env: { LIVE_SERVER_SECRET_KEY: "synthetic-test-secret" } }));

const ID = "83d1fc7e-f22f-4310-9975-a24964785bd3";
const OLD_ASSET = "a11f084a-fb1d-47fb-b57c-b7f1232ea928";
const NEW_ASSET = "e522ef32-18ea-4527-8e0d-708bed6780e4";
const encode = (value: Uint8Array) => Buffer.from(value).toString("base64");
const original = () => getBinaryDataFromDocumentEditorHTMLString("<p>OLD</p>", "Title");

describe("canonical Page content endpoints", () => {
  let listener: Server;
  let origin: string;
  let document: Document;
  let rooms: Map<string, Document>;
  beforeEach(async () => {
    document = new Document(ID);
    Y.applyUpdate(document, original());
    document.isLoading = false;
    const app = express();
    app.use("/admin/documents", ...pageContentMiddleware);
    rooms = new Map([[ID, document]]);
    registerController(app, PageContentController, [{ documents: rooms }]);
    listener = app.listen(0, "127.0.0.1");
    await new Promise<void>((resolve) => listener.once("listening", resolve));
    origin = `http://127.0.0.1:${(listener.address() as AddressInfo).port}`;
  });
  afterEach(async () => {
    document.destroy();
    listener.closeAllConnections();
    await new Promise<void>((resolve, reject) =>
      listener.close((error) => {
        if (error) {
          reject(error);
          return;
        }
        resolve();
      })
    );
  });

  const post = (baseUrl: string, action: string, body: object, secret = "synthetic-test-secret") =>
    fetch(`${baseUrl}/admin/documents/${ID}/${action}`, {
      method: "POST",
      headers: { "content-type": "application/json", "live-server-secret-key": secret },
      body: JSON.stringify(body),
    });

  it("prepares without changing the warm room, then publishes one replacement and merges stale fallback saves", async () => {
    const stale = Y.encodeStateAsUpdate(document);
    const response = await post(origin, "prepare-pat", {
      persisted_binary: encode(stale),
      previous_html: "<p>OLD</p>",
      title: "Title",
      replacement_html: "<p>NEW</p>",
    });
    expect(response.status).toBe(200);
    const prepared = (await response.json()) as ReturnType<typeof mergePageContentSnapshot>;
    expect(Y.encodeStateAsUpdate(document)).toEqual(stale);
    const publish = await post(origin, "publish-pat", { description_binary: prepared.description_binary });
    expect(publish.status).toBe(200);
    expect(await publish.json()).toEqual({ status: "published" });
    // Each save deliberately follows the previous acknowledgement, as a client
    // repeatedly falling back after a reconnect would do.
    /* eslint-disable no-await-in-loop */
    for (let attempt = 0; attempt < 3; attempt++) {
      const merged = await post(origin, "merge-snapshot", {
        persisted_binary: prepared.description_binary,
        incoming_binary: encode(stale),
      });
      expect(merged.status).toBe(200);
      const content = (await merged.json()) as ReturnType<typeof mergePageContentSnapshot>;
      expect(content.description_html).toContain("NEW");
      expect(content.description_html).not.toContain("OLD");
      expect(
        mergePageContentSnapshot({
          persistedBinary: Buffer.from(content.description_binary, "base64"),
          incomingBinary: Y.encodeStateAsUpdate(document),
        }).description_html
      ).toBe(content.description_html);
    }
    /* eslint-enable no-await-in-loop */
  });

  it("requires the internal secret before parsing JSON and never accepts browser credentials as that secret", async () => {
    const response = await fetch(`${origin}/admin/documents/${ID}/prepare-pat`, {
      method: "POST",
      headers: { "content-type": "application/json", cookie: "sessionid=synthetic" },
      body: "{invalid json",
    });
    expect(response.status).toBe(401);
    expect((await post(origin, "publish-pat", { description_binary: encode(original()) }, "wrong")).status).toBe(401);
  });

  it("prepares asset src remapping in a warm room without mutating it", async () => {
    Y.applyUpdate(
      document,
      getBinaryDataFromDocumentEditorHTMLString(`<image-component src="${OLD_ASSET}" alt="Kept"></image-component>`)
    );
    const before = Y.encodeStateAsUpdate(document);
    const response = await post(origin, "prepare-assets", {
      persisted_binary: encode(before),
      previous_html: "<p>Fallback</p>",
      title: "Ignored title",
      duplicated_assets: [{ old_asset_id: OLD_ASSET, new_asset_id: NEW_ASSET }],
    });
    expect(response.status).toBe(200);
    const content = (await response.json()) as ReturnType<typeof mergePageContentSnapshot>;
    expect(content.description_html).toContain(NEW_ASSET);
    expect(content.description_html).not.toContain(OLD_ASSET);
    expect(content.description_html).toContain("OLD");
    expect(Y.encodeStateAsUpdate(document)).toEqual(before);
    expect(
      getAllDocumentFormatsFromDocumentEditorBinaryData(Buffer.from(content.description_binary, "base64"), true)
        .titleHTML
    ).toBe("Title");
  });

  it.each([false, true])("handles title-only prepare with preserve_loaded_title=%s", async (preserveLoadedTitle) => {
    const before = Y.encodeStateAsUpdate(document);
    const response = await post(origin, "prepare-pat", {
      persisted_binary: encode(before),
      previous_html: "<p>Stale</p>",
      title: "Requested title",
      replace_title: true,
      preserve_loaded_title: preserveLoadedTitle,
    });
    expect(response.status).toBe(200);
    const content = (await response.json()) as ReturnType<typeof mergePageContentSnapshot>;
    const formats = getAllDocumentFormatsFromDocumentEditorBinaryData(
      Buffer.from(content.description_binary, "base64"),
      true
    );
    expect(formats.titleHTML).toBe(preserveLoadedTitle ? "Title" : "Requested title");
    expect(formats.contentHTML).toContain("OLD");
    expect(formats.contentJSON).toEqual(getAllDocumentFormatsFromDocumentEditorBinaryData(before, false).contentJSON);
    if (preserveLoadedTitle) expect(Buffer.from(content.description_binary, "base64")).toEqual(Buffer.from(before));
    expect(Y.encodeStateAsUpdate(document)).toEqual(before);
  });

  it.each([false, true])(
    "does not treat an initializing Document as an authoritative warm title (has title=%s)",
    async (hasTitle) => {
      const initializing = new Document(ID);
      try {
        const persisted = original();
        if (hasTitle) Y.applyUpdate(initializing, persisted);
        expect(initializing.isLoading).toBe(true);
        rooms.set(ID, initializing);
        const before = Y.encodeStateAsUpdate(initializing);
        const response = await post(origin, "prepare-pat", {
          persisted_binary: encode(persisted),
          previous_html: "<p>Stale</p>",
          title: "Cold requested title",
          replace_title: true,
          preserve_loaded_title: true,
        });
        expect(response.status).toBe(200);
        const content = (await response.json()) as ReturnType<typeof mergePageContentSnapshot>;
        const formats = getAllDocumentFormatsFromDocumentEditorBinaryData(
          Buffer.from(content.description_binary, "base64"),
          true
        );
        expect(formats.titleHTML).toBe("Cold requested title");
        expect(formats.contentHTML).toContain("OLD");
        expect(Y.encodeStateAsUpdate(initializing)).toEqual(before);
      } finally {
        initializing.destroy();
      }
    }
  );

  it("preserves a ready warm intentionally deleted title without creating title IDs", async () => {
    const title = document.getXmlFragment("title");
    title.delete(0, title.length);
    const before = Y.encodeStateAsUpdate(document);
    const response = await post(origin, "prepare-pat", {
      persisted_binary: encode(before),
      previous_html: "<p>Stale</p>",
      title: "Callback title",
      replace_title: true,
      preserve_loaded_title: true,
    });
    expect(response.status).toBe(200);
    const content = (await response.json()) as ReturnType<typeof mergePageContentSnapshot>;
    expect(Buffer.from(content.description_binary, "base64")).toEqual(Buffer.from(before));
    expect(
      getAllDocumentFormatsFromDocumentEditorBinaryData(Buffer.from(content.description_binary, "base64"), true)
        .titleHTML
    ).toBe("");
  });

  it("updates cold canonical titles even when preserve_loaded_title is requested", async () => {
    const response = await fetch(`${origin}/admin/documents/33209f56-a7b5-4e63-80ab-35a1dcdd5971/prepare-pat`, {
      method: "POST",
      headers: { "content-type": "application/json", "live-server-secret-key": "synthetic-test-secret" },
      body: JSON.stringify({
        persisted_binary: encode(original()),
        previous_html: "<p>Stale</p>",
        title: "Cold requested title",
        replace_title: true,
        preserve_loaded_title: true,
      }),
    });
    expect(response.status).toBe(200);
    const content = (await response.json()) as ReturnType<typeof mergePageContentSnapshot>;
    const formats = getAllDocumentFormatsFromDocumentEditorBinaryData(
      Buffer.from(content.description_binary, "base64"),
      true
    );
    expect(formats.titleHTML).toBe("Cold requested title");
    expect(formats.contentHTML).toContain("OLD");
  });

  it("authenticates asset requests and validates strict bounded UUID mappings", async () => {
    const body = {
      persisted_binary: encode(original()),
      previous_html: "",
      title: "Title",
      duplicated_assets: [] as object[],
    };
    expect((await post(origin, "prepare-assets", body, "wrong")).status).toBe(401);
    const mapping = { old_asset_id: OLD_ASSET, new_asset_id: NEW_ASSET };
    const invalidBodies = [
      { ...body, unknown: true },
      { ...body, duplicated_assets: [{ ...mapping, unknown: true }] },
      { ...body, duplicated_assets: [{ old_asset_id: "https://synthetic.invalid/asset", new_asset_id: NEW_ASSET }] },
      { ...body, duplicated_assets: Array.from({ length: 10001 }, () => mapping) },
    ];
    await Promise.all(
      invalidBodies.map(async (payload) => expect((await post(origin, "prepare-assets", payload)).status).toBe(400))
    );
    expect(
      (
        await post(origin, "prepare-assets", {
          ...body,
          duplicated_assets: Array.from({ length: 10000 }, () => mapping),
        })
      ).status
    ).toBe(200);
  });

  it("does not echo malformed authenticated JSON or document fragments in errors", async () => {
    const response = await fetch(`${origin}/admin/documents/${ID}/prepare-pat`, {
      method: "POST",
      headers: { "content-type": "application/json", "live-server-secret-key": "synthetic-test-secret" },
      body: '{"PRIVATE_SYNTHETIC_CONTENT":broken}',
    });
    expect(response.status).toBe(400);
    const body = await response.text();
    expect(body).not.toContain("PRIVATE_SYNTHETIC_CONTENT");
    expect(body).not.toContain("SyntaxError");
  });

  it("rejects invalid IDs, malformed base64 and empty snapshots without mutating a room", async () => {
    const before = encode(Y.encodeStateAsUpdate(document));
    await Promise.all(
      ["", "%%", "AA==", "AAA=", "AAAA".repeat(100000)].map(async (incoming_binary) => {
        const response = await post(origin, "merge-snapshot", { persisted_binary: before, incoming_binary });
        expect(response.status).toBe(400);
      })
    );
    expect(
      (
        await fetch(`${origin}/admin/documents/not-a-uuid/publish-pat`, {
          method: "POST",
          headers: { "content-type": "application/json", "live-server-secret-key": "synthetic-test-secret" },
          body: JSON.stringify({ description_binary: before }),
        })
      ).status
    ).toBe(400);
    expect(encode(Y.encodeStateAsUpdate(document))).toBe(before);
  });

  it.each([4, 10])(
    "accepts real Yjs snapshots near %i MiB through merge and publish",
    async (mebibytes) => {
      const large = new Y.Doc();
      try {
        Y.applyUpdate(large, Y.encodeStateAsUpdate(document));
        const paddingLength = mebibytes * 1024 * 1024 - 1024;
        large.getMap("syntheticLarge").set("padding", "x".repeat(paddingLength));
        const binary = Y.encodeStateAsUpdate(large);
        expect(binary.byteLength).toBeGreaterThan(mebibytes * 1024 * 1024 - 2048);
        expect(binary.byteLength).toBeLessThanOrEqual(10 * 1024 * 1024);
        const encoded = encode(binary);
        const [merged, published] = await Promise.all([
          post(origin, "merge-snapshot", { persisted_binary: "", incoming_binary: encoded }),
          post(origin, "publish-pat", { description_binary: encoded }),
        ]);
        expect(merged.status).toBe(200);
        expect(published.status).toBe(200);
        const content = (await merged.json()) as ReturnType<typeof mergePageContentSnapshot>;
        expect(Buffer.from(content.description_binary, "base64").byteLength).toBe(binary.byteLength);
        expect(content.description_html.match(/OLD/g)).toHaveLength(1);
        expect((document.getMap("syntheticLarge").get("padding") as string).length).toBe(paddingLength);
      } finally {
        large.destroy();
      }
    },
    20000
  );

  it("rejects malformed base64 padding and nonalphabet characters", async () => {
    await Promise.all(
      ["A===", "=AAA", "AA=A", "AAA==", "AAAA=", "AA==AAAA", "AAAA\n", "AA-A", "AA_A"].map(async (incoming_binary) => {
        const response = await post(origin, "merge-snapshot", { persisted_binary: "", incoming_binary });
        expect(response.status).toBe(400);
      })
    );
  });

  it("acknowledges a cold publish without creating an unpersisted room", async () => {
    const server = { documents: new Map() };
    const controller = new PageContentController(server as unknown as Hocuspocus);
    const res = { status: vi.fn(), json: vi.fn() };
    res.status.mockReturnValue(res);
    controller.publish(
      { params: { documentId: ID }, body: { description_binary: encode(original()) } } as never,
      res as never
    );
    expect(res.json).toHaveBeenCalledWith({ status: "not_loaded" });
    expect(server.documents.size).toBe(0);
  });
});

it("publishes through Hocuspocus onChange without scheduling cookie-context database stores", async () => {
  vi.useFakeTimers();
  const onChange = vi.fn();
  const onStoreDocument = vi.fn();
  const debounce = 2000;
  const maxDebounce = 10000;
  const server = new Hocuspocus({ onChange, onStoreDocument, quiet: true, debounce, maxDebounce });
  try {
    const document = await server.createDocument(ID, { headers: {}, url: "/" }, "test", {
      readOnly: false,
      requiresAuthentication: false,
      isAuthenticated: true,
    });
    const controller = new PageContentController(server);
    const res = { status: vi.fn(), json: vi.fn() };
    res.status.mockReturnValue(res);
    controller.publish(
      { params: { documentId: ID }, body: { description_binary: encode(original()) } } as never,
      res as never
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ transactionOrigin: null }));
    await vi.advanceTimersByTimeAsync(maxDebounce + 1);
    expect(onStoreDocument).not.toHaveBeenCalled();

    // Positive control: the same real Hocuspocus update/debounce path must save
    // when an update carries a normal connection origin and cookie context.
    const connectionOrigin = {
      context: { cookie: "synthetic-session" },
      socketId: "synthetic-client",
      request: { headers: {}, url: "/" },
    };
    document.transact(() => document.getMap("synthetic-control").set("value", "Client edit"), connectionOrigin);
    await vi.advanceTimersByTimeAsync(0);
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ transactionOrigin: connectionOrigin }));
    await vi.advanceTimersByTimeAsync(debounce - 1);
    expect(onStoreDocument).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(onStoreDocument).toHaveBeenCalledOnce();
    expect(onStoreDocument).toHaveBeenCalledWith(
      expect.objectContaining({ transactionOrigin: connectionOrigin, context: connectionOrigin.context })
    );
  } finally {
    try {
      await Promise.all([...server.documents.values()].map((document) => server.unloadDocument(document)));
      await server.destroy();
    } finally {
      vi.useRealTimers();
    }
  }
});
