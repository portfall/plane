/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// components
import { MermaidViewerModal } from "../src/core/extensions/code/mermaid-viewer-modal";

vi.mock("@plane/propel/tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => children,
}));

const SVG = '<svg viewBox="0 0 100 100"><text>Diagram</text></svg>';

describe("MermaidViewerModal", () => {
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

  const renderViewer = (onClose = vi.fn()) => {
    act(() => root.render(<MermaidViewerModal isOpen onClose={onClose} svg={SVG} />));
    return onClose;
  };

  it("zooms with controls and keyboard, then resets the view", () => {
    renderViewer();

    const zoomIn = document.querySelector<HTMLButtonElement>('[aria-label="Zoom in"]');
    const stage = document.querySelector<HTMLDivElement>(".mermaid-diagram-viewer-stage");
    expect(zoomIn).not.toBeNull();
    expect(stage).not.toBeNull();

    act(() => zoomIn?.click());
    expect(document.body.textContent).toContain("125%");

    act(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "+" })));
    expect(document.body.textContent).toContain("150%");

    act(() => stage?.dispatchEvent(new WheelEvent("wheel", { bubbles: true, deltaY: 1 })));
    expect(document.body.textContent).toContain("140%");

    act(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "0" })));
    expect(document.body.textContent).toContain("100%");
  });

  it("gives percentage-sized Mermaid SVGs a viewport-sized centering box", () => {
    renderViewer();

    const renderedSvg = document.querySelector<HTMLDivElement>(".mermaid-diagram-viewer-svg");

    expect(renderedSvg).not.toBeNull();
    expect(renderedSvg?.classList).toContain("size-full");
  });

  it("pans an enlarged diagram and clears the offset on reset", () => {
    renderViewer();

    const stage = document.querySelector<HTMLDivElement>(".mermaid-diagram-viewer-stage");
    const renderedSvg = document.querySelector<HTMLDivElement>(".mermaid-diagram-viewer-svg");
    expect(stage).not.toBeNull();
    expect(renderedSvg).not.toBeNull();
    if (!stage || !renderedSvg) throw new Error("Viewer did not render");

    stage.setPointerCapture = vi.fn();
    stage.hasPointerCapture = vi.fn(() => true);
    stage.releasePointerCapture = vi.fn();

    act(() => document.querySelector<HTMLButtonElement>('[aria-label="Zoom in"]')?.click());
    act(() =>
      stage.dispatchEvent(
        new PointerEvent("pointerdown", {
          bubbles: true,
          button: 0,
          clientX: 10,
          clientY: 15,
          isPrimary: true,
          pointerId: 1,
        })
      )
    );
    act(() =>
      stage.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: 35, clientY: 45, pointerId: 1 }))
    );
    act(() => stage.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, pointerId: 1 })));

    expect(renderedSvg.style.transform).toBe("translate(25px, 30px) scale(1.25)");

    act(() => document.querySelector<HTMLButtonElement>('[aria-label^="Reset zoom"]')?.click());
    expect(renderedSvg.style.transform).toBe("translate(0px, 0px) scale(1)");
  });

  it("closes from the explicit close control", () => {
    const onClose = renderViewer();

    act(() => document.querySelector<HTMLButtonElement>('[aria-label="Close diagram viewer"]')?.click());

    expect(onClose).toHaveBeenCalledOnce();
  });
});
