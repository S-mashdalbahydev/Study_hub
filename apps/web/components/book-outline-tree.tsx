"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { Book } from "@/lib/api";
import { plainTitle } from "./study-heading";

type TocNode = Book["toc"][number] & { children: TocNode[] };

function hierarchy(items: Book["toc"]): TocNode[] {
  const nodes = new Map(
    items.map((item) => [item.id, { ...item, children: [] } as TocNode]),
  );
  const roots: TocNode[] = [];
  for (const item of items) {
    const node = nodes.get(item.id)!;
    const parent = item.parent_id ? nodes.get(item.parent_id) : undefined;
    if (parent && parent !== node) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}

function ancestors(id: string | null, items: Book["toc"]): string[] {
  const byId = new Map(items.map((item) => [item.id, item]));
  const result: string[] = [];
  let current = id ? byId.get(id) : undefined;
  while (current?.parent_id) {
    result.push(current.parent_id);
    current = byId.get(current.parent_id);
  }
  return result;
}

export function BookOutlineTree({
  book,
  page,
  onNavigate,
}: {
  book: Book;
  page: number;
  onNavigate: (page: number, id: string) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const nodes = useMemo(() => hierarchy(book.toc), [book.toc]);
  const location = useMemo(() => {
    const candidates = book.toc.filter((item) => item.reader_page <= page);
    return candidates.at(-1)?.id || null;
  }, [book.toc, page]);
  const [selected, setSelected] = useState<string | null>(location);
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(ancestors(location, book.toc)),
  );

  useEffect(() => {
    // Keep an explicitly selected node when several semantic nodes share a reader page.
    const selectedItem = book.toc.find((item) => item.id === selected);
    const next = selectedItem?.reader_page === page ? selected : location;
    setSelected(next);
    setExpanded(
      (current) => new Set([...current, ...ancestors(next, book.toc)]),
    );
  }, [page, location, book.toc, selected]);

  function toggle(id: string, open?: boolean) {
    setExpanded((current) => {
      const next = new Set(current);
      const shouldOpen = open ?? !next.has(id);
      if (shouldOpen) next.add(id);
      else next.delete(id);
      return next;
    });
  }
  function focusRelative(element: HTMLElement, offset: number) {
    const visible = Array.from(
      root.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') || [],
    ).filter((item) => item.offsetParent !== null);
    const index = visible.indexOf(element);
    visible[Math.max(0, Math.min(visible.length - 1, index + offset))]?.focus();
  }
  function render(node: TocNode, depth = 0) {
    const hasChildren = node.children.length > 0;
    const open = expanded.has(node.id);
    const active = selected === node.id;
    return (
      <div
        key={node.id}
        role="treeitem"
        aria-level={depth + 1}
        aria-expanded={hasChildren ? open : undefined}
        aria-selected={active}
        tabIndex={active || (!selected && node === nodes[0]) ? 0 : -1}
        data-node-id={node.id}
        className={`outline-tree-item ${active ? "active" : ""}`}
        onKeyDown={(event) => {
          const element = event.currentTarget;
          if (event.key === "ArrowDown") {
            event.preventDefault();
            focusRelative(element, 1);
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            focusRelative(element, -1);
          } else if (event.key === "ArrowRight") {
            event.preventDefault();
            if (hasChildren && !open) toggle(node.id, true);
            else
              element
                .querySelector<HTMLElement>(
                  ':scope > [role="group"] > [role="treeitem"]',
                )
                ?.focus();
          } else if (event.key === "ArrowLeft") {
            event.preventDefault();
            if (hasChildren && open) toggle(node.id, false);
            else
              element.parentElement
                ?.closest<HTMLElement>('[role="treeitem"]')
                ?.focus();
          } else if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            setSelected(node.id);
            onNavigate(node.reader_page, node.id);
          }
        }}
      >
        <div
          className={`outline-tree-row kind-${node.kind}`}
          style={{ paddingLeft: 8 + depth * 15 }}
        >
          {hasChildren ? (
            <button
              type="button"
              className="tree-disclosure"
              aria-label={`${open ? "Collapse" : "Expand"} ${plainTitle(node.title)}`}
              onClick={(event) => {
                event.stopPropagation();
                toggle(node.id);
              }}
            >
              {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
          ) : (
            <span className="tree-spacer" />
          )}
          <button
            type="button"
            className="tree-label"
            onClick={() => {
              setSelected(node.id);
              onNavigate(node.reader_page, node.id);
            }}
          >
            <span className="tree-title" dir="auto">
              {plainTitle(node.title)}
            </span>
            <span className="tree-page">{node.reader_page}</span>
          </button>
        </div>
        {hasChildren && open && (
          <div role="group">
            {node.children.map((child) => render(child, depth + 1))}
          </div>
        )}
      </div>
    );
  }
  return (
    <div
      ref={root}
      className="outline-tree"
      role="tree"
      aria-label="Book outline"
    >
      {nodes.map((node) => render(node))}
    </div>
  );
}
