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

const SVG = '<svg id="mermaid-rendered" viewBox="0 0 100 100"><text>Diagram</text></svg>';

vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async () => ({ svg: SVG })),
  },
}));

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

const MERMAID_NODE = {
  attrs: { id: "diagram-1", language: "mermaid" },
  nodeSize: 24,
  textContent: "flowchart TD\n  A --> B",
} as unknown as ProseMirrorNode;

describe("MermaidDiagram editor interactions", () => {
  let container: HTMLDivElement;
  let root: Root;
  let parentMouseDown: EventListener | undefined;

  beforeEach(() => {
    vi.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    if (parentMouseDown) document.body.removeEventListener("mousedown", parentMouseDown);
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  it("opens one SVG viewer without moving the editor selection", async () => {
    let selectionUpdate: (() => void) | undefined;
    const editor = {
      isEditable: true,
      off: vi.fn(),
      on: vi.fn((event: string, callback: () => void) => {
        if (event === "selectionUpdate") selectionUpdate = callback;
      }),
      state: { selection: { from: 30 } },
    } as unknown as Editor;
    const selectionMouseDown = vi.fn(() => {
      (editor.state.selection as { from: number }).from = 2;
      selectionUpdate?.();
    });
    parentMouseDown = selectionMouseDown;
    document.body.addEventListener("mousedown", parentMouseDown);

    act(() => root.render(<CodeBlockComponent editor={editor} getPos={() => 0} node={MERMAID_NODE} />));
    await act(async () => vi.advanceTimersByTimeAsync(400));

    const preview = document.querySelector<HTMLButtonElement>(".mermaid-diagram-open");
    expect(preview?.disabled).toBe(false);
    expect(container.querySelector("pre")?.classList).toContain("mermaid-code-source-hidden");
    if (!preview) throw new Error("Mermaid preview did not render");

    const mouseDown = new MouseEvent("mousedown", { bubbles: true, cancelable: true });
    act(() => preview.dispatchEvent(mouseDown));

    expect(mouseDown.defaultPrevented).toBe(true);
    expect(selectionMouseDown).not.toHaveBeenCalled();
    expect(container.querySelector("pre")?.classList).toContain("mermaid-code-source-hidden");

    act(() => preview.click());

    expect(document.querySelector('[aria-label="Close diagram viewer"]')).not.toBeNull();
    expect(document.querySelectorAll("#mermaid-rendered")).toHaveLength(1);
    expect(document.querySelector(".mermaid-diagram-open")).toBeNull();
    expect(container.querySelector("pre")?.classList).toContain("mermaid-code-source-hidden");

    act(() => document.querySelector<HTMLButtonElement>('[aria-label="Close diagram viewer"]')?.click());

    expect(document.querySelector('[aria-label="Close diagram viewer"]')).toBeNull();
    expect(container.querySelector("pre")?.classList).toContain("mermaid-code-source-hidden");
  });
});
