/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/** Offline, identity-only inspection. This codec never rewrites or re-encodes a document. */
import { Buffer } from "buffer";
import { getSchema, Node as EditorNode } from "@tiptap/core";
import { generateJSON } from "@tiptap/html";
import type { Schema } from "@tiptap/pm/model";
import { yXmlFragmentToProseMirrorRootNode } from "y-prosemirror";
import * as Y from "yjs";
import { BLOCK_NODE_TYPES } from "@/constants/extension";
import { ADDITIONAL_BLOCK_NODE_TYPES } from "@/plane-editor/constants/extensions";
import { CoreEditorExtensionsWithoutProps, DocumentEditorExtensionsWithoutProps } from "../extensions/core-without-props";
import { TitleExtensions } from "../extensions/title-extension";

export const PROJECT_BUNDLE_DOCUMENT_FORMAT = "plane-project-document/1";
export const PROJECT_BUNDLE_DOCUMENT_SUPPORT = "identity-literals/2";
export const PROJECT_BUNDLE_LEGACY_FORMAT = "plane-project-legacy-document/1";
export const PROJECT_BUNDLE_MAX_BYTES = 10 * 1024 * 1024;
const MAX_STRUCTS = 100_000;
const MAX_REFERENCES = 20_000;
const kinds = new Set(["asset", "user", "issue", "page", "project", "workspace"]);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
// Literal text may contain external URLs and UUIDs (for example links to other systems); anything
// naming the source instance, by origin or by bare host, still refuses until it is rewritten.
// Plane's interactive editor has two extensions the server-side schema lacks: UniqueID gives each
// block a random UUID `id`, and tiptap-markdown flags lists `tight`. Neither refers to anything.
const uniqueIdNodes = new Set<string>([...BLOCK_NODE_TYPES, ...ADDITIONAL_BLOCK_NODE_TYPES]);
const editorAttribute = (node: string, key: string, value: unknown): boolean =>
  (key === "id" && uniqueIdNodes.has(node) && (value === null || (typeof value === "string" && uuid.test(value)))) ||
  (key === "tight" && (node === "bulletList" || node === "orderedList") && (value === null || typeof value === "boolean"));
// Attribute values become HTML attributes and, for colours and styles, CSS: none may load a
// resource, so no fetching CSS function, @import or escape (which could spell one) is accepted.
const cssInert = (value: string): boolean => !/(?:url|image-set|image|src|element|cross-fade|expression)\s*\(|@import|\\/i.test(value);
const inertAttribute = (value: unknown, depth = 0): boolean =>
  depth < 64 &&
  (typeof value === "string" ? cssInert(value)
    : Array.isArray(value) ? value.every((child) => inertAttribute(child, depth + 1))
    : object(value) ? Object.values(value).every((child) => inertAttribute(child, depth + 1))
    : true);
// A colour as Plane writes it. Plane renders these attributes into style (custom colour marks
// and table cells, headers and rows), and the obsolete HTML `background` attribute would load a
// URL, so each must be a single colour value: never another declaration.
const colour = (value: string): boolean =>
  /^(none|transparent|#[0-9a-f]{3,8}|[a-z][a-z0-9-]*|(rgb|rgba|hsl|hsla)\([0-9.,%\s]+\)|var\(--[a-z0-9-]+\))$/i.test(value.trim());
const styleAttributes = new Set(["color", "backgroundColor", "background", "textColor"]);
const styleColour = (key: string, value: unknown): boolean =>
  !styleAttributes.has(key) || value === null || (typeof value === "string" && colour(value));
const styleColours = (attrs: Record<string, unknown>): boolean => Object.entries(attrs).every(([key, value]) => styleColour(key, value));
// A link may only lead to the web or to mail, parsed as a browser does; relative, scheme-less and
// script URLs refuse.
const safeHref = (value: unknown): boolean => {
  if (value === null || value === undefined) return true;
  if (typeof value !== "string") return false;
  try {
    return ["https:", "http:", "mailto:"].includes(new URL(value).protocol);
  } catch {
    return false;
  }
};
// Legacy inline styles: only presentational properties with plain values.
const safeStyle = (value: string): boolean =>
  value.split(";").every((declaration) => {
    if (!declaration.trim()) return true;
    const [property, ...rest] = declaration.split(":");
    return rest.length === 1 && ["color", "background-color", "text-align", "width", "min-width"].includes(property.trim().toLowerCase()) &&
      /^[#a-z0-9(),.%\s-]*$/i.test(rest[0]) && cssInert(rest[0]);
  });
const namesSource = (value: string, sourceOrigin: string): boolean => {
  const lowered = value.toLowerCase();
  return lowered.includes(sourceOrigin.toLowerCase()) || lowered.includes(new URL(sourceOrigin).host.toLowerCase());
};
const nodes = new Set("doc text paragraph heading blockquote bulletList orderedList listItem taskList taskItem codeBlock hardBreak horizontalRule image imageComponent mention emoji calloutComponent table tableRow tableCell tableHeader issue-embed-component".split(" "));
const marks = new Set("bold italic strike code underline textStyle customColor link".split(" "));

type Kind = "asset" | "user" | "issue" | "page" | "project" | "workspace";
export type ProjectBundleDocumentReference = { kind: Kind; source: string; target: string };
export type ProjectBundleDocumentRequest = {
  format: typeof PROJECT_BUNDLE_DOCUMENT_FORMAT;
  variant: "rich" | "document";
  binary: string;
  source_origin: string;
  target_origin: string;
  references: ProjectBundleDocumentReference[];
};
export type ProjectBundleLegacyRequest = Omit<ProjectBundleDocumentRequest, "format" | "binary"> & {
  format: typeof PROJECT_BUNDLE_LEGACY_FORMAT;
  html_base64: string | null;
  json_base64: string | null;
};
type Reference = ProjectBundleDocumentReference & { location: string; retained: boolean };
export type ProjectBundleDocumentErrorCode =
  | "INVALID_INPUT" | "LIMIT_EXCEEDED" | "INVALID_DOCUMENT" | "INCOMPLETE_HISTORY"
  | "UNKNOWN_ROOT" | "UNKNOWN_TYPE" | "UNKNOWN_ATTRIBUTE" | "UNKNOWN_MARK"
  | "UNSUPPORTED_HISTORY" | "AMBIGUOUS_HISTORY" | "UNCLASSIFIED_REFERENCE"
  | "UNMAPPED_REFERENCE" | "REFERENCE_REWRITE_REQUIRED" | "ORIGIN_REWRITE_REQUIRED" | "UNSUPPORTED_HTML";

export class ProjectBundleDocumentError extends Error {
  constructor(public readonly code: ProjectBundleDocumentErrorCode) {
    super(code);
    this.name = "ProjectBundleDocumentError";
  }
}

/** Inspect legacy fields without replacing their original representation.
 * The extra parser rule inventories the raw DOM and always declines to parse a
 * node. The guarded projection must equal the unmodified native parser's output.
 */
export async function inspectProjectBundleLegacyDocument(input: ProjectBundleLegacyRequest) {
  try {
    requireDocument(exactKeys(input, ["format", "variant", "html_base64", "json_base64", "source_origin", "target_origin", "references"]), "INVALID_INPUT");
    requireDocument(input.format === PROJECT_BUNDLE_LEGACY_FORMAT && ["rich", "document"].includes(input.variant), "INVALID_INPUT");
    try { origin(input.source_origin); origin(input.target_origin); } catch { throw new ProjectBundleDocumentError("INVALID_INPUT"); }
    const decode = (value: unknown) => {
      if (value === null) return null;
      requireDocument(typeof value === "string" && value.length <= Math.ceil(PROJECT_BUNDLE_MAX_BYTES / 3) * 4, "INVALID_INPUT");
      const bytes = Buffer.from(value, "base64");
      requireDocument(bytes.toString("base64") === value && bytes.length <= PROJECT_BUNDLE_MAX_BYTES, "INVALID_INPUT");
      return bytes;
    };
    const htmlBytes = decode(input.html_base64);
    const jsonBytes = decode(input.json_base64);
    requireDocument((htmlBytes?.length ?? 0) + (jsonBytes?.length ?? 0) <= PROJECT_BUNDLE_MAX_BYTES, "LIMIT_EXCEEDED");
    const extensions = input.variant === "rich" ? CoreEditorExtensionsWithoutProps : [...CoreEditorExtensionsWithoutProps, ...DocumentEditorExtensionsWithoutProps];
    const schema = getSchema(extensions);
    const mappings = new Map<string, string>();
    requireDocument(Array.isArray(input.references) && input.references.length <= MAX_REFERENCES, "INVALID_INPUT");
    for (const ref of input.references) {
      requireDocument(exactKeys(ref, ["kind", "source", "target"]) && kinds.has(ref.kind), "INVALID_INPUT");
      for (const value of [ref.source, ref.target]) requireDocument(typeof value === "string" && (ref.kind === "workspace" || ref.kind === "project" ? /^[A-Za-z0-9_-]{1,128}$/.test(value) : uuid.test(value)), "INVALID_INPUT");
      const key = `${ref.kind}/${ref.source}`;
      requireDocument(!mappings.has(key), "INVALID_INPUT");
      mappings.set(key, ref.target);
    }
    const references: Reference[] = [];
    const literal = (value: unknown, depth = 0): void => {
      requireDocument(depth < 64, "LIMIT_EXCEEDED");
      if (typeof value === "string") {
        requireDocument(input.source_origin === input.target_origin || !namesSource(value, input.source_origin), "ORIGIN_REWRITE_REQUIRED");
      } else if (Array.isArray(value)) for (const child of value) literal(child, depth + 1);
      else if (object(value)) for (const child of Object.values(value)) literal(child, depth + 1);
      else requireDocument(value === null || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value)), "UNSUPPORTED_HISTORY");
    };
    const reference = (kind: Kind, value: unknown, location: string) => {
      if (value === null || value === undefined || value === "") return;
      requireDocument(typeof value === "string", "UNCLASSIFIED_REFERENCE");
      requireDocument(input.source_origin === input.target_origin || !namesSource(value, input.source_origin), "ORIGIN_REWRITE_REQUIRED");
      const target = mappings.get(`${kind}/${value}`);
      requireDocument(target !== undefined, "UNMAPPED_REFERENCE");
      requireDocument(target === value, "REFERENCE_REWRITE_REQUIRED");
      requireDocument(references.length < MAX_REFERENCES, "LIMIT_EXCEEDED");
      references.push({ kind, source: value, target, location, retained: false });
    };
    let count = 0;
    const walk = (value: unknown, location: string, depth = 0): void => {
      requireDocument(depth < 64 && count++ < MAX_STRUCTS && object(value), "INVALID_DOCUMENT");
      requireDocument(Object.keys(value).every((key) => ["type", "attrs", "content", "marks", "text"].includes(key)), "UNKNOWN_ATTRIBUTE");
      requireDocument(typeof value.type === "string" && nodes.has(value.type) && !!schema.nodes[value.type], "UNKNOWN_TYPE");
      if (value.attrs !== undefined && value.attrs !== null) {
        requireDocument(object(value.attrs), "INVALID_DOCUMENT");
        for (const [key, attr] of Object.entries(value.attrs)) {
          requireDocument(Object.hasOwn(schema.nodes[value.type].spec.attrs ?? {}, key) || editorAttribute(value.type, key, attr), "UNKNOWN_ATTRIBUTE");
          requireDocument(inertAttribute(attr) && styleColour(key, attr), "UNCLASSIFIED_REFERENCE");
          if (["image", "imageComponent"].includes(value.type) && key === "src") reference("asset", attr, `${location}/src`);
          else if (["mention", "issue-embed-component"].includes(value.type) && key === "entity_identifier") {
            const kind = value.attrs.entity_name;
            if (attr !== null && attr !== "") {
              requireDocument(kind === "user" || kind === "issue" || kind === "page", "UNCLASSIFIED_REFERENCE");
              reference(kind, attr, `${location}/entity_identifier`);
            }
          } else if (value.type === "issue-embed-component" && key === "workspace_identifier") reference("workspace", attr, `${location}/workspace_identifier`);
          else if (value.type === "issue-embed-component" && key === "project_identifier") reference("project", attr, `${location}/project_identifier`);
          else if (key === "id" && value.type !== "mention") requireDocument(attr === null || (typeof attr === "string" && !attr.includes("://")), "UNCLASSIFIED_REFERENCE");
          else literal(attr);
        }
      }
      if (value.text !== undefined) { requireDocument(typeof value.text === "string", "INVALID_DOCUMENT"); literal(value.text); }
      if (value.marks !== undefined) {
        requireDocument(Array.isArray(value.marks), "INVALID_DOCUMENT");
        for (const mark of value.marks) {
          requireDocument(object(mark) && Object.keys(mark).every((key) => ["type", "attrs"].includes(key)) && typeof mark.type === "string" && marks.has(mark.type) && !!schema.marks[mark.type], "UNKNOWN_MARK");
          if (mark.attrs !== undefined && mark.attrs !== null) {
            requireDocument(object(mark.attrs), "INVALID_DOCUMENT");
            for (const key of Object.keys(mark.attrs)) requireDocument(Object.hasOwn(schema.marks[mark.type].spec.attrs ?? {}, key), "UNKNOWN_ATTRIBUTE");
            literal(mark.attrs);
            requireDocument(inertAttribute(mark.attrs) && styleColours(mark.attrs), "UNCLASSIFIED_REFERENCE");
            if (mark.type === "link") requireDocument(safeHref(mark.attrs.href), "UNCLASSIFIED_REFERENCE");
          }
        }
      }
      if (value.content !== undefined) {
        requireDocument(Array.isArray(value.content), "INVALID_DOCUMENT");
        value.content.forEach((child, index) => walk(child, `${location}/content/${index}`, depth + 1));
      }
    };
    let elements = 0;
    const tags = new Set("p h1 h2 h3 h4 h5 h6 blockquote ul ol li pre code br hr img image-component mention-component issue-embed-component span em strong b i u s del strike a table thead tbody tfoot tr td th div label input".split(" "));
    const attributes = new Set("id data-id class style background spellcheck title alt src href target rel width height aspectratio alignment status entity_identifier entity_name project_identifier workspace_identifier data-type data-checked data-color checked type color colspan rowspan colwidth scope start textalign".split(" "));
    const guardElement = (element: HTMLElement, depth = 0): void => {
      requireDocument(depth < 64 && elements++ < MAX_STRUCTS, "LIMIT_EXCEEDED");
      const tag = element.tagName.toLowerCase();
      requireDocument(tags.has(tag), "UNSUPPORTED_HTML");
      for (const attr of Array.from(element.attributes)) {
        const key = attr.name.toLowerCase();
        requireDocument(attributes.has(key), "UNKNOWN_ATTRIBUTE");
        requireDocument(cssInert(attr.value), "UNCLASSIFIED_REFERENCE");
        if (key === "href") { literal(attr.value); requireDocument(safeHref(attr.value), "UNCLASSIFIED_REFERENCE"); }
        else if (key === "style") { literal(attr.value); requireDocument(safeStyle(attr.value), "UNCLASSIFIED_REFERENCE"); }
        else if (["background", "color", "data-color"].includes(key)) requireDocument(colour(attr.value), "UNCLASSIFIED_REFERENCE");
        else if ((tag === "image-component" || tag === "img") && key === "src") reference("asset", attr.value, `html/${elements}/src`);
        else if ((tag === "mention-component" || tag === "issue-embed-component") && key === "entity_identifier") {
          const kind = element.getAttribute("entity_name");
          requireDocument(kind === "user" || kind === "issue" || kind === "page", "UNCLASSIFIED_REFERENCE");
          reference(kind, attr.value, `html/${elements}/entity_identifier`);
        } else if (tag === "issue-embed-component" && key === "workspace_identifier") reference("workspace", attr.value, `html/${elements}/workspace_identifier`);
        else if (tag === "issue-embed-component" && key === "project_identifier") reference("project", attr.value, `html/${elements}/project_identifier`);
        else if (["id", "data-id"].includes(key) && tag !== "mention-component") requireDocument(!attr.value.includes("://"), "UNCLASSIFIED_REFERENCE");
        else literal(attr.value);
      }
      for (const child of Array.from(element.childNodes)) {
        if (child.nodeType === 1) guardElement(child as HTMLElement, depth + 1);
        else if (child.nodeType === 3) literal(child.textContent);
        else requireDocument(false, "UNSUPPORTED_HTML");
      }
    };
    if (htmlBytes !== null) {
      const html = new TextDecoder("utf-8", { fatal: true }).decode(htmlBytes);
      requireDocument(!html.includes("<!--"), "UNSUPPORTED_HTML");
      const inspected = new WeakSet<object>();
      const guard = EditorNode.create({ name: "projectBundleOfflineGuard", group: "block", atom: true, parseHTML: () => [{ tag: "*", priority: 100000, getAttrs: (element) => {
        requireDocument(typeof element !== "string", "UNSUPPORTED_HTML");
        if (!inspected.has(element)) { guardElement(element); inspected.add(element); }
        return false;
      } }] });
      const guarded = generateJSON(html, [...extensions, guard]);
      const native = generateJSON(html, extensions);
      requireDocument(JSON.stringify(guarded) === JSON.stringify(native), "UNSUPPORTED_HTML");
      walk(native, "html/native");
    }
    if (jsonBytes !== null) {
      const value: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(jsonBytes));
      if (value !== null && !(object(value) && Object.keys(value).length === 0)) {
        walk(value, "json");
        const parsed = schema.nodeFromJSON(value);
        if (parsed.type.name !== "doc" || parsed.childCount) parsed.check();
      }
    }
    return { format: PROJECT_BUNDLE_LEGACY_FORMAT, status: "accepted" as const, support: PROJECT_BUNDLE_DOCUMENT_SUPPORT, variant: input.variant,
      html_base64: input.html_base64, json_base64: input.json_base64,
      html_sha256: htmlBytes === null ? null : await hash(htmlBytes), json_sha256: jsonBytes === null ? null : await hash(jsonBytes),
      schema_sha256: await hash(schemaDescription(schema) + schemaDescription(getSchema(TitleExtensions))), references,
      census: { elements, nodes: count } };
  } catch (error) {
    if (error instanceof ProjectBundleDocumentError) throw error;
    throw new ProjectBundleDocumentError("INVALID_DOCUMENT");
  }
}
function requireDocument(condition: unknown, code: ProjectBundleDocumentErrorCode): asserts condition {
  if (!condition) throw new ProjectBundleDocumentError(code);
}
const hash = async (value: Uint8Array | string) => {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : new Uint8Array(value);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
};
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
const exactKeys = (value: unknown, keys: string[]) => object(value) && Object.keys(value).sort().join(",") === keys.sort().join(",");
function origin(value: unknown) {
  requireDocument(typeof value === "string", "INVALID_INPUT");
  const parsed = new URL(value);
  requireDocument(parsed.protocol === "https:" && parsed.origin === value && !parsed.username && !parsed.password, "INVALID_INPUT");
}
function schemaDescription(schema: Schema) {
  const describe = (types: Schema["nodes"] | Schema["marks"]) => Object.entries(types).sort(([a], [b]) => a.localeCompare(b)).map(([name, type]) => ({ name, spec: { content: type.spec.content, group: type.spec.group, inline: type.spec.inline, atom: type.spec.atom, inclusive: type.spec.inclusive, excludes: type.spec.excludes }, attrs: type.spec.attrs }));
  // Native specs include functions; their source participates in the fingerprint.
  return JSON.stringify({ nodes: describe(schema.nodes), marks: describe(schema.marks) }, (_key, value) => typeof value === "function" ? value.toString() : value);
}

export async function inspectProjectBundleDocument(input: ProjectBundleDocumentRequest) {
  try {
    return await inspect(input);
  } catch (error) {
    if (error instanceof ProjectBundleDocumentError) throw error;
    throw new ProjectBundleDocumentError("INVALID_DOCUMENT");
  }
}

async function inspect(input: ProjectBundleDocumentRequest) {
  requireDocument(exactKeys(input, ["format", "variant", "binary", "source_origin", "target_origin", "references"]), "INVALID_INPUT");
  requireDocument(input.format === PROJECT_BUNDLE_DOCUMENT_FORMAT && ["rich", "document"].includes(input.variant), "INVALID_INPUT");
  requireDocument(typeof input.binary === "string", "INVALID_INPUT");
  requireDocument(input.binary.length <= Math.ceil(PROJECT_BUNDLE_MAX_BYTES / 3) * 4, "LIMIT_EXCEEDED");
  requireDocument(input.binary.length > 0 && /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(input.binary), "INVALID_INPUT");
  const bytes = Buffer.from(input.binary, "base64");
  requireDocument(bytes.length <= PROJECT_BUNDLE_MAX_BYTES && bytes.toString("base64") === input.binary, "INVALID_INPUT");
  try { origin(input.source_origin); origin(input.target_origin); } catch { throw new ProjectBundleDocumentError("INVALID_INPUT"); }
  requireDocument(Array.isArray(input.references) && input.references.length <= MAX_REFERENCES, "INVALID_INPUT");
  const mappings = new Map<string, string>();
  for (const ref of input.references) {
    requireDocument(exactKeys(ref, ["kind", "source", "target"]) && kinds.has(ref.kind), "INVALID_INPUT");
    for (const value of [ref.source, ref.target]) {
      requireDocument(typeof value === "string" && (ref.kind === "workspace" || ref.kind === "project" ? /^[A-Za-z0-9_-]{1,128}$/.test(value) : uuid.test(value)), "INVALID_INPUT");
    }
    const key = `${ref.kind}/${ref.source}`;
    requireDocument(!mappings.has(key), "INVALID_INPUT");
    mappings.set(key, ref.target);
  }
  const extensions = input.variant === "rich" ? CoreEditorExtensionsWithoutProps : [...CoreEditorExtensionsWithoutProps, ...DocumentEditorExtensionsWithoutProps];
  const schema = getSchema(extensions);
  const titleSchema = getSchema(TitleExtensions);
  const schema_sha256 = await hash(schemaDescription(schema) + schemaDescription(titleSchema));
  const decoded = Y.decodeUpdate(bytes);
  requireDocument(decoded.structs.length <= MAX_STRUCTS && decoded.ds.clients.size <= MAX_STRUCTS, "LIMIT_EXCEEDED");
  const doc = new Y.Doc({ gc: false });
  try {
    doc.getXmlFragment("default");
    if (input.variant === "document") doc.getXmlFragment("title");
    Y.applyUpdate(doc, bytes);
    requireDocument(doc.store.pendingStructs === null && doc.store.pendingDs === null, "INCOMPLETE_HISTORY");
    const roots = [...doc.share.keys()].sort();
    requireDocument(roots.every((name) => name === "default" || (input.variant === "document" && name === "title")), "UNKNOWN_ROOT");
    const references: Reference[] = [];
    const retained = (item: Y.Item) => {
      let cursor: Y.Item | null = item;
      let depth = 0;
      while (cursor) {
        requireDocument(depth++ < 64, "LIMIT_EXCEEDED");
        if (cursor.deleted) return true;
        requireDocument(cursor.parent instanceof Y.AbstractType, "UNSUPPORTED_HISTORY");
        cursor = cursor.parent._item;
      }
      return false;
    };
    const rootSchema = (type: Y.AbstractType<any>) => {
      let cursor = type;
      let depth = 0;
      while (cursor._item) {
        requireDocument(depth++ < 64 && cursor._item.parent instanceof Y.AbstractType, "UNSUPPORTED_HISTORY");
        cursor = cursor._item.parent;
      }
      requireDocument(cursor instanceof Y.XmlFragment && !(cursor instanceof Y.XmlElement), "UNKNOWN_TYPE");
      const root = Y.findRootTypeKey(cursor);
      requireDocument(root === "default" || (input.variant === "document" && root === "title"), "UNKNOWN_ROOT");
      return root === "title" ? titleSchema : schema;
    };
    const literal = (value: unknown, depth = 0): void => {
      requireDocument(depth < 64, "LIMIT_EXCEEDED");
      if (typeof value === "string") {
        requireDocument(input.source_origin === input.target_origin || !namesSource(value, input.source_origin), "ORIGIN_REWRITE_REQUIRED");
      } else if (Array.isArray(value)) for (const child of value) literal(child, depth + 1);
      else if (object(value)) for (const child of Object.values(value)) literal(child, depth + 1);
      else requireDocument(value === null || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value)), "UNSUPPORTED_HISTORY");
    };
    const reference = (kind: Kind, source: unknown, item: Y.Item, location: string) => {
      if (source === null || source === undefined || source === "") return;
      requireDocument(typeof source === "string", "UNCLASSIFIED_REFERENCE");
      requireDocument(input.source_origin === input.target_origin || !namesSource(source, input.source_origin), "ORIGIN_REWRITE_REQUIRED");
      const target = mappings.get(`${kind}/${source}`);
      requireDocument(target !== undefined, "UNMAPPED_REFERENCE");
      requireDocument(target === source, "REFERENCE_REWRITE_REQUIRED");
      requireDocument(references.length < MAX_REFERENCES, "LIMIT_EXCEEDED");
      references.push({ kind, source, target, retained: retained(item), location });
    };

    // Resolve attribute roles only when every retained value agrees. Independent
    // CRDT registers cannot establish which historical entity kind paired with an ID.
    const entityKinds = new Map<Y.AbstractType<any>, Set<unknown>>();
    for (const raw of decoded.structs) {
      if (!(raw instanceof Y.Item)) continue;
      const item = Y.getItem(doc.store, raw.id);
      if (item instanceof Y.Item && raw.parentSub === "entity_name" && raw.content instanceof Y.ContentAny && item.parent instanceof Y.AbstractType) {
        const values = entityKinds.get(item.parent) ?? new Set();
        for (const value of raw.content.getContent()) if (value !== null) values.add(value);
        entityKinds.set(item.parent, values);
      }
    }
    let retained_structs = 0;
    let gc_structs = 0;
    const texts = new Set<Y.XmlText>();
    for (const raw of decoded.structs) {
      if (raw instanceof Y.GC) { gc_structs++; continue; }
      requireDocument(raw instanceof Y.Item, "UNSUPPORTED_HISTORY");
      const item = Y.getItem(doc.store, raw.id);
      if (item instanceof Y.GC && raw.content instanceof Y.ContentDeleted) { gc_structs++; continue; }
      requireDocument(item instanceof Y.Item && item.parent instanceof Y.AbstractType, "UNSUPPORTED_HISTORY");
      if (retained(item)) retained_structs++;
      const parent = item.parent;
      const nativeSchema = rootSchema(parent);
      const location = `${raw.id.client}:${raw.id.clock}`;
      const content = raw.content;
      if (content instanceof Y.ContentDeleted) continue;
      if (content instanceof Y.ContentType) {
        const type = content.type;
        requireDocument(raw.parentSub === null, "UNKNOWN_TYPE");
        if (type instanceof Y.XmlElement) requireDocument(nodes.has(type.nodeName) && !!nativeSchema.nodes[type.nodeName], "UNKNOWN_TYPE");
        else requireDocument(type instanceof Y.XmlText, "UNKNOWN_TYPE");
      } else if (content instanceof Y.ContentAny) {
        requireDocument(parent instanceof Y.XmlElement && typeof raw.parentSub === "string", "UNSUPPORTED_HISTORY");
        const name = parent.nodeName;
        requireDocument(nodes.has(name) && !!nativeSchema.nodes[name], "UNKNOWN_TYPE");
        requireDocument(Object.hasOwn((nativeSchema.nodes[name].spec.attrs ?? {}), raw.parentSub) || editorAttribute(name, raw.parentSub, content.getContent()[0]), "UNKNOWN_ATTRIBUTE");
        const values = content.getContent();
        requireDocument(values.length === 1, "UNSUPPORTED_HISTORY");
        const value = values[0];
        requireDocument(inertAttribute(value) && styleColour(raw.parentSub, value), "UNCLASSIFIED_REFERENCE");
        if ((name === "imageComponent" || name === "image") && raw.parentSub === "src") reference("asset", value, item, location);
        else if ((name === "mention" || name === "issue-embed-component") && raw.parentSub === "entity_identifier") {
          const values = entityKinds.get(parent);
          requireDocument(values?.size === 1, "AMBIGUOUS_HISTORY");
          const kind = [...values][0];
          requireDocument(kind === "user" || kind === "issue" || kind === "page", "UNCLASSIFIED_REFERENCE");
          reference(kind, value, item, location);
        } else if (name === "issue-embed-component" && raw.parentSub === "workspace_identifier") reference("workspace", value, item, location);
        else if (name === "issue-embed-component" && raw.parentSub === "project_identifier") reference("project", value, item, location);
        else if (raw.parentSub === "id" && name !== "mention") {
          requireDocument(value === null || (typeof value === "string" && !value.includes("://")), "UNCLASSIFIED_REFERENCE");
        } else literal(value);
      } else if (content instanceof Y.ContentFormat) {
        requireDocument(parent instanceof Y.XmlText && marks.has(content.key) && !!nativeSchema.marks[content.key], "UNKNOWN_MARK");
        if (content.value !== null) {
          requireDocument(object(content.value), "UNSUPPORTED_HISTORY");
          for (const key of Object.keys(content.value)) requireDocument(Object.hasOwn((nativeSchema.marks[content.key].spec.attrs ?? {}), key), "UNKNOWN_ATTRIBUTE");
          literal(content.value);
          requireDocument(inertAttribute(content.value) && styleColours(content.value), "UNCLASSIFIED_REFERENCE");
          if (content.key === "link") requireDocument(safeHref(content.value.href), "UNCLASSIFIED_REFERENCE");
        }
      } else if (content instanceof Y.ContentString) {
        requireDocument(parent instanceof Y.XmlText, "UNSUPPORTED_HISTORY");
        texts.add(parent);
      } else throw new ProjectBundleDocumentError("UNSUPPORTED_HISTORY");
    }
    // Include deleted text and split runs when rejecting opaque references.
    for (const text of texts) {
      let combined = "";
      let count = 0;
      for (let item = text._start; item; item = item.right) {
        requireDocument(count++ < MAX_STRUCTS, "LIMIT_EXCEEDED");
        if (item.content instanceof Y.ContentString) combined += item.content.str;
      }
      literal(combined);
    }
    if (doc.getXmlFragment("default").length) yXmlFragmentToProseMirrorRootNode(doc.getXmlFragment("default"), schema).check();
    if (input.variant === "document" && doc.getXmlFragment("title").length) yXmlFragmentToProseMirrorRootNode(doc.getXmlFragment("title"), titleSchema).check();
    const before_sha256 = await hash(bytes);
    return {
      format: PROJECT_BUNDLE_DOCUMENT_FORMAT,
      status: "accepted" as const,
      support: PROJECT_BUNDLE_DOCUMENT_SUPPORT,
      variant: input.variant,
      binary: input.binary,
      before_sha256,
      after_sha256: before_sha256,
      schema_sha256,
      references,
      census: { roots, structs: decoded.structs.length, retained_structs, gc_structs },
    };
  } finally {
    doc.destroy();
  }
}
