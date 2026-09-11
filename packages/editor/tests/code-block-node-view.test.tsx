/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { Node as ProseMirrorNode } from "@tiptap/pm/model";
import type { Editor } from "@tiptap/react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// components
import { CodeBlockComponent } from "../src/core/extensions/code/code-block-node-view";

vi.mock("@plane/propel/tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("@tiptap/react", async () => {
  const actual = await vi.importActual<typeof import("@tiptap/react")>("@tiptap/react");
  return {
    ...actual,
    NodeViewContent: () => <code data-testid="mermaid-source" />,
    NodeViewWrapper: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  };
});

vi.mock("../src/core/extensions/code/mermaid-diagram", () => ({
  MermaidDiagram: ({ canEdit, onToggleSource }: { canEdit: boolean; onToggleSource: () => void }) => (
    <div data-can-edit={String(canEdit)} data-testid="mermaid-preview">
      {canEdit && (
        <button type="button" onClick={onToggleSource}>
          Edit source
        </button>
      )}
    </div>
  ),
}));

const MERMAID_NODE = {
  attrs: { id: "diagram-1", language: "mermaid" },
  nodeSize: 24,
  textContent: "flowchart TD\n  A --> B",
} as unknown as ProseMirrorNode;

const editor = (isEditable: boolean, selectionFrom = 30) =>
  ({
    isEditable,
    off: vi.fn(),
    on: vi.fn(),
    state: { selection: { from: selectionFrom } },
  }) as unknown as Editor;

describe("CodeBlockComponent Mermaid presentation", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("shows the rendered diagram first and reveals source explicitly while editing", () => {
    act(() => root.render(<CodeBlockComponent editor={editor(true)} getPos={() => 0} node={MERMAID_NODE} />));

    expect(container.querySelector('[data-testid="mermaid-preview"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="mermaid-source"]')).not.toBeNull();
    expect(container.querySelector("pre")?.classList).toContain("mermaid-code-source-hidden");
    expect(container.querySelector("pre")?.getAttribute("aria-hidden")).toBe("true");

    act(() => container.querySelector<HTMLButtonElement>("button")?.click());

    expect(container.querySelector("pre")?.classList).not.toContain("mermaid-code-source-hidden");
    expect(container.querySelector("pre")?.getAttribute("aria-hidden")).toBe("false");
  });

  it("keeps raw source hidden in read-only mode", () => {
    act(() => root.render(<CodeBlockComponent editor={editor(false)} getPos={() => 0} node={MERMAID_NODE} />));

    expect(container.querySelector('[data-testid="mermaid-preview"]')?.getAttribute("data-can-edit")).toBe("false");
    expect(container.querySelector('[data-testid="mermaid-source"]')).not.toBeNull();
    expect(container.querySelector("pre")?.classList).toContain("mermaid-code-source-hidden");
    expect(container.querySelector("pre")?.getAttribute("aria-hidden")).toBe("true");
    expect(container.querySelector("button")).toBeNull();
  });

  it("reveals source when the editor selection enters the Mermaid block", () => {
    act(() => root.render(<CodeBlockComponent editor={editor(true, 2)} getPos={() => 0} node={MERMAID_NODE} />));

    expect(container.querySelector("pre")?.classList).not.toContain("mermaid-code-source-hidden");
  });
});
