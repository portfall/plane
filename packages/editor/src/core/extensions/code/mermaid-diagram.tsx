/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Check, Code2, Copy, EyeOff, Maximize2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
// ui
import { Tooltip } from "@plane/propel/tooltip";
// plane utils
import { formatMermaidError, getMermaidTheme, cn } from "@plane/utils";
// components
import { MermaidViewerModal } from "./mermaid-viewer-modal";

type Props = {
  canEdit: boolean;
  copied: boolean;
  isSourceVisible: boolean;
  onCopy: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onToggleSource: () => void;
  source: string;
};

// debounce delay (ms) before re-rendering the diagram while the source is being edited
const RENDER_DEBOUNCE_MS = 400;

// mermaid is dynamically imported on the client to keep it out of the initial bundle
let mermaidModulePromise: Promise<typeof import("mermaid").default> | undefined;
const loadMermaid = () => {
  mermaidModulePromise ??= import("mermaid").then((module) => module.default);
  return mermaidModulePromise;
};

let initializedTheme: string | undefined;
let renderIdCounter = 0;

const getThemeAttribute = (element: HTMLElement | null): string | null => {
  const themedAncestor = element?.closest("[data-theme]");
  if (themedAncestor) return themedAncestor.getAttribute("data-theme");
  return document.documentElement.getAttribute("data-theme");
};

export function MermaidDiagram({ canEdit, copied, isSourceVisible, onCopy, onToggleSource, source }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  // states
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isViewerOpen, setIsViewerOpen] = useState(false);
  const [svg, setSvg] = useState("");
  const [themeAttribute, setThemeAttribute] = useState<string | null>(null);

  // track the active `data-theme` (set by next-themes) so the diagram re-renders on theme changes
  useEffect(() => {
    const resolveTheme = () => setThemeAttribute(getThemeAttribute(rootRef.current));
    resolveTheme();
    const observer = new MutationObserver(resolveTheme);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  // render the diagram, debounced, whenever the source or theme changes
  useEffect(() => {
    if (!source.trim()) {
      setError(null);
      setIsLoading(false);
      setIsViewerOpen(false);
      setSvg("");
      return;
    }

    let cancelled = false;
    setIsLoading(true);
    setIsViewerOpen(false);
    setSvg("");

    const renderTimer = setTimeout(() => {
      void (async () => {
        try {
          const mermaid = await loadMermaid();
          if (cancelled) return;

          const theme = getMermaidTheme(themeAttribute);
          if (initializedTheme !== theme) {
            mermaid.initialize({ securityLevel: "strict", startOnLoad: false, theme });
            initializedTheme = theme;
          }

          const { svg: renderedSvg } = await mermaid.render(`mermaid-diagram-${++renderIdCounter}`, source);
          if (cancelled) return;

          setSvg(renderedSvg);
          setError(null);
          setIsLoading(false);
        } catch (err) {
          if (cancelled) return;
          setError(formatMermaidError(err));
          setIsLoading(false);
          setIsViewerOpen(false);
          setSvg("");
        }
      })();
    }, RENDER_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      clearTimeout(renderTimer);
    };
  }, [source, themeAttribute]);
  const state = error ? "error" : isLoading ? "loading" : "rendered";
  return (
    <>
      <div
        ref={rootRef}
        className={cn("mermaid-diagram group/mermaid", { "mermaid-diagram-error": error })}
        data-mermaid-state={state}
      >
        <div className="shadow-sm absolute top-2 right-2 z-10 flex items-center gap-1 rounded-md border border-subtle bg-layer-1/90 p-1 opacity-100 backdrop-blur-sm transition-opacity duration-150 sm:opacity-0 sm:group-focus-within/mermaid:opacity-100 sm:group-hover/mermaid:opacity-100">
          <Tooltip tooltipContent={copied ? "Copied" : "Copy source"}>
            <button
              type="button"
              className="focus-visible:outline-accent-primary grid size-8 place-items-center rounded text-tertiary transition-colors duration-150 hover:bg-layer-3 hover:text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
              onClick={onCopy}
              aria-label={copied ? "Mermaid source copied" : "Copy Mermaid source"}
            >
              {copied ? <Check className="size-3.5 text-success-primary" /> : <Copy className="size-3.5" />}
            </button>
          </Tooltip>
          {canEdit && (
            <Tooltip tooltipContent={isSourceVisible ? "Hide source" : "Edit source"}>
              <button
                type="button"
                className="focus-visible:outline-accent-primary grid size-8 place-items-center rounded text-tertiary transition-colors duration-150 hover:bg-layer-3 hover:text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  onToggleSource();
                }}
                aria-label={isSourceVisible ? "Hide Mermaid source" : "Edit Mermaid source"}
                aria-pressed={isSourceVisible}
              >
                {isSourceVisible ? <EyeOff className="size-3.5" /> : <Code2 className="size-3.5" />}
              </button>
            </Tooltip>
          )}
          <Tooltip tooltipContent="Open interactive viewer">
            <button
              type="button"
              className="focus-visible:outline-accent-primary grid size-8 place-items-center rounded text-tertiary transition-colors duration-150 hover:bg-layer-3 hover:text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:text-disabled"
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                setIsViewerOpen(true);
              }}
              disabled={!svg || !!error}
              aria-label="Open Mermaid diagram in interactive viewer"
            >
              <Maximize2 className="size-3.5" />
            </button>
          </Tooltip>
        </div>
        {error && (
          <>
            <p className="mermaid-diagram-error-message">{error}</p>
            {!isSourceVisible && (
              <pre className="mermaid-diagram-source">
                <code>{source}</code>
              </pre>
            )}
          </>
        )}
        {!error && isLoading && <div className="mermaid-diagram-loading">Rendering diagram…</div>}
        <button
          type="button"
          className="mermaid-diagram-open"
          onClick={() => setIsViewerOpen(true)}
          disabled={!svg || !!error}
          aria-label="Open Mermaid diagram in interactive viewer"
          hidden={!!error || isViewerOpen}
        >
          <div
            className="mermaid-diagram-canvas"
            // Mermaid renders with securityLevel=strict before the SVG reaches this sink.
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        </button>
      </div>
      <MermaidViewerModal isOpen={isViewerOpen} onClose={() => setIsViewerOpen(false)} svg={svg} />
    </>
  );
}
