/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { Node as ProseMirrorNode } from "@tiptap/pm/model";
import type { Editor } from "@tiptap/react";
import { NodeViewWrapper, NodeViewContent } from "@tiptap/react";
import ts from "highlight.js/lib/languages/typescript";
import { common, createLowlight } from "lowlight";
import { CheckIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { CopyIcon } from "@plane/propel/icons";
// ui
import { Tooltip } from "@plane/propel/tooltip";
// plane utils
import { cn, isMermaidLanguage } from "@plane/utils";
// types
import type { TCodeBlockAttributes } from "./types";
import { ECodeBlockAttributeNames } from "./types";
// components
import { MermaidDiagram } from "./mermaid-diagram";

// we just have ts support for now
const lowlight = createLowlight(common);
lowlight.register("ts", ts);

type Props = {
  editor: Editor;
  getPos: () => number | undefined;
  node: ProseMirrorNode;
};

export function CodeBlockComponent({ editor, getPos, node }: Props) {
  const [copied, setCopied] = useState(false);
  // derived values
  const attrs = node.attrs as TCodeBlockAttributes;
  const isMermaid = isMermaidLanguage(attrs[ECodeBlockAttributeNames.LANGUAGE]);

  const isSelectionInsideNode = useCallback(() => {
    const nodePosition = getPos();
    if (typeof nodePosition !== "number") return false;
    const { from } = editor.state.selection;
    return from > nodePosition && from < nodePosition + node.nodeSize;
  }, [editor, getPos, node.nodeSize]);

  const [isMermaidSourceVisible, setIsMermaidSourceVisible] = useState(
    () => editor.isEditable && (!node.textContent.trim() || isSelectionInsideNode())
  );

  useEffect(() => {
    if (!editor.isEditable || !isMermaid || isMermaidSourceVisible) return;

    const revealSourceForSelection = () => {
      if (isSelectionInsideNode()) setIsMermaidSourceVisible(true);
    };

    editor.on("selectionUpdate", revealSourceForSelection);
    revealSourceForSelection();
    return () => {
      editor.off("selectionUpdate", revealSourceForSelection);
    };
  }, [editor, isMermaid, isMermaidSourceVisible, isSelectionInsideNode]);

  const copyToClipboard = async (e: React.MouseEvent<HTMLButtonElement, MouseEvent>) => {
    try {
      await navigator.clipboard.writeText(node.textContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 1000);
    } catch {
      setCopied(false);
    }
    e.preventDefault();
    e.stopPropagation();
  };

  return (
    <NodeViewWrapper key={attrs[ECodeBlockAttributeNames.ID]} className="code-block group/code relative">
      {!isMermaid && (
        <Tooltip tooltipContent="Copy code">
          <button
            type="button"
            className={cn(
              "group/button absolute top-2 right-2 z-10 hidden size-8 items-center justify-center rounded-md border border-subtle bg-layer-1 backdrop-blur-sm transition duration-150 ease-in-out group-hover/code:flex",
              {
                "bg-success-subtle hover:bg-success-subtle-1 active:bg-success-subtle-1": copied,
              }
            )}
            onClick={(e) => void copyToClipboard(e)}
          >
            {copied ? (
              <CheckIcon className="h-3 w-3 text-success-primary" strokeWidth={3} />
            ) : (
              <CopyIcon className="h-3 w-3 text-tertiary group-hover/button:text-primary" />
            )}
          </button>
        </Tooltip>
      )}

      <pre
        className={cn("my-2 rounded-lg bg-layer-3 p-4 text-primary", {
          "mermaid-code-source-hidden": isMermaid && !isMermaidSourceVisible,
        })}
        aria-hidden={isMermaid && !isMermaidSourceVisible}
      >
        <NodeViewContent as="code" className="whitespace-pre-wrap" />
      </pre>

      {isMermaid && (
        <MermaidDiagram
          canEdit={editor.isEditable}
          copied={copied}
          isSourceVisible={isMermaidSourceVisible}
          onCopy={(event) => void copyToClipboard(event)}
          onToggleSource={() => setIsMermaidSourceVisible((isVisible) => !isVisible)}
          source={node.textContent}
        />
      )}
    </NodeViewWrapper>
  );
}
