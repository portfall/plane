/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Dialog, Transition } from "@headlessui/react";
import { Maximize2, Minus, Move, Plus, RotateCcw, X } from "lucide-react";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
// ui
import { Tooltip } from "@plane/propel/tooltip";
// plane utils
import { clampMermaidZoom, getNextMermaidZoom, MERMAID_MAX_ZOOM, MERMAID_MIN_ZOOM, cn } from "@plane/utils";

type TPoint = {
  x: number;
  y: number;
};

type Props = {
  isOpen: boolean;
  onClose: () => void;
  svg: string;
};

const INITIAL_OFFSET: TPoint = { x: 0, y: 0 };
const INITIAL_VIEW = { offset: INITIAL_OFFSET, zoom: 100 };

export function MermaidViewerModal({ isOpen, onClose, svg }: Props) {
  const dragStartRef = useRef<TPoint>(INITIAL_OFFSET);
  const offsetStartRef = useRef<TPoint>(INITIAL_OFFSET);
  // states
  const [isDragging, setIsDragging] = useState(false);
  const [stageElement, setStageElement] = useState<HTMLDivElement | null>(null);
  const [{ offset, zoom }, setView] = useState(INITIAL_VIEW);

  const resetView = useCallback(() => {
    setIsDragging(false);
    setView(INITIAL_VIEW);
  }, []);

  const updateZoom = useCallback((getNextZoom: (currentZoom: number) => number) => {
    setView((currentView) => {
      const nextZoom = clampMermaidZoom(getNextZoom(currentView.zoom));
      return { offset: nextZoom <= 100 ? INITIAL_OFFSET : currentView.offset, zoom: nextZoom };
    });
  }, []);

  const stepZoom = useCallback(
    (direction: "increase" | "decrease") => updateZoom((currentZoom) => getNextMermaidZoom(currentZoom, direction)),
    [updateZoom]
  );

  useEffect(() => {
    if (isOpen) resetView();
  }, [isOpen, resetView, svg]);

  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.key === "+" || event.key === "=") {
        event.preventDefault();
        stepZoom("increase");
      } else if (event.key === "-") {
        event.preventDefault();
        stepZoom("decrease");
      } else if (event.key === "0") {
        event.preventDefault();
        resetView();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, resetView, stepZoom]);

  useEffect(() => {
    if (!isOpen || !stageElement) return;

    const handleWheel = (event: WheelEvent) => {
      if (event.deltaY === 0) return;
      event.preventDefault();
      updateZoom((currentZoom) => currentZoom + (event.deltaY < 0 ? 10 : -10));
    };

    stageElement.addEventListener("wheel", handleWheel, { passive: false });
    return () => stageElement.removeEventListener("wheel", handleWheel);
  }, [isOpen, stageElement, updateZoom]);

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (zoom <= 100 || event.button !== 0 || !event.isPrimary) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragStartRef.current = { x: event.clientX, y: event.clientY };
    offsetStartRef.current = offset;
    setIsDragging(true);
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!isDragging) return;
    if (zoom <= 100) {
      setIsDragging(false);
      return;
    }
    setView((currentView) => ({
      ...currentView,
      offset: {
        x: offsetStartRef.current.x + event.clientX - dragStartRef.current.x,
        y: offsetStartRef.current.y + event.clientY - dragStartRef.current.y,
      },
    }));
  };

  const handlePointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    setIsDragging(false);
  };

  return (
    <Transition.Root show={isOpen} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={onClose}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-200"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-150"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/80 backdrop-blur-sm" />
        </Transition.Child>

        <div className="fixed inset-0 z-50 p-3 sm:p-6">
          <Transition.Child
            as={Fragment}
            enter="ease-out duration-200"
            enterFrom="scale-[0.98] opacity-0"
            enterTo="scale-100 opacity-100"
            leave="ease-in duration-150"
            leaveFrom="scale-100 opacity-100"
            leaveTo="scale-[0.98] opacity-0"
          >
            <Dialog.Panel className="relative flex size-full flex-col overflow-hidden rounded-lg border border-subtle-1 bg-layer-1 shadow-raised-200">
              <div className="flex h-12 flex-shrink-0 items-center justify-between border-b border-subtle-1 px-4">
                <div className="flex min-w-0 items-center gap-2">
                  <Maximize2 className="size-4 flex-shrink-0 text-tertiary" aria-hidden="true" />
                  <Dialog.Title className="truncate text-13 font-medium text-primary">Mermaid diagram</Dialog.Title>
                  <span className="hidden text-11 text-tertiary sm:inline">Scroll to zoom · drag to pan</span>
                </div>
                <Tooltip tooltipContent="Close (Esc)">
                  <button
                    type="button"
                    className="focus-visible:outline-accent-primary grid size-9 place-items-center rounded-md text-tertiary transition-colors duration-150 hover:bg-layer-3 hover:text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                    onClick={onClose}
                    aria-label="Close diagram viewer"
                  >
                    <X className="size-4" aria-hidden="true" />
                  </button>
                </Tooltip>
              </div>

              <div
                ref={setStageElement}
                className={cn(
                  "mermaid-diagram-viewer-stage relative flex min-h-0 flex-1 touch-none items-center justify-center overflow-hidden bg-layer-2",
                  zoom > 100 && (isDragging ? "cursor-grabbing" : "cursor-grab")
                )}
                onPointerDown={handlePointerDown}
                onPointerMove={handlePointerMove}
                onPointerUp={handlePointerUp}
                onPointerCancel={handlePointerUp}
                onLostPointerCapture={handlePointerUp}
                onContextMenu={() => setIsDragging(false)}
              >
                <div
                  className="mermaid-diagram-viewer-svg select-none"
                  style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom / 100})` }}
                  // Mermaid renders with securityLevel=strict; this is the same SVG already shown in the inline preview.
                  dangerouslySetInnerHTML={{ __html: svg }}
                />
              </div>

              <div className="pointer-events-none absolute bottom-5 left-1/2 -translate-x-1/2">
                <div className="pointer-events-auto flex h-11 items-center gap-1 rounded-lg border border-subtle-1 bg-layer-1/95 px-1.5 shadow-raised-200 backdrop-blur-sm">
                  <Tooltip tooltipContent="Zoom out (-)">
                    <button
                      type="button"
                      className="focus-visible:outline-accent-primary grid size-9 place-items-center rounded-md text-secondary transition-colors duration-150 hover:bg-layer-3 hover:text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:text-disabled"
                      onClick={() => stepZoom("decrease")}
                      disabled={zoom <= MERMAID_MIN_ZOOM}
                      aria-label="Zoom out"
                    >
                      <Minus className="size-4" aria-hidden="true" />
                    </button>
                  </Tooltip>
                  <button
                    type="button"
                    className="focus-visible:outline-accent-primary h-9 min-w-14 rounded-md px-2 text-12 font-medium text-primary tabular-nums transition-colors duration-150 hover:bg-layer-3 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                    onClick={resetView}
                    aria-label={`Reset zoom from ${zoom} percent`}
                  >
                    {zoom}%
                  </button>
                  <Tooltip tooltipContent="Zoom in (+)">
                    <button
                      type="button"
                      className="focus-visible:outline-accent-primary grid size-9 place-items-center rounded-md text-secondary transition-colors duration-150 hover:bg-layer-3 hover:text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:text-disabled"
                      onClick={() => stepZoom("increase")}
                      disabled={zoom >= MERMAID_MAX_ZOOM}
                      aria-label="Zoom in"
                    >
                      <Plus className="size-4" aria-hidden="true" />
                    </button>
                  </Tooltip>
                  <div className="bg-subtle-1 mx-0.5 h-5 w-px" />
                  <Tooltip tooltipContent="Reset view (0)">
                    <button
                      type="button"
                      className="focus-visible:outline-accent-primary grid size-9 place-items-center rounded-md text-secondary transition-colors duration-150 hover:bg-layer-3 hover:text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                      onClick={resetView}
                      aria-label="Reset diagram view"
                    >
                      <RotateCcw className="size-4" aria-hidden="true" />
                    </button>
                  </Tooltip>
                  <span className="sr-only" aria-live="polite">
                    Diagram zoom {zoom} percent
                  </span>
                </div>
              </div>

              {zoom > 100 && (
                <div className="pointer-events-none absolute top-16 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-subtle-1 bg-layer-1/90 px-3 py-1.5 text-11 text-tertiary backdrop-blur-sm">
                  <Move className="size-3" aria-hidden="true" />
                  Drag to pan
                </div>
              )}
            </Dialog.Panel>
          </Transition.Child>
        </div>
      </Dialog>
    </Transition.Root>
  );
}
