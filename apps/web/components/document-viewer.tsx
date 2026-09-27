"use client";
import { useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import type {
  EventBus as PdfEventBus,
  PDFViewer as PdfViewerEngine,
} from "pdfjs-dist/web/pdf_viewer.mjs";
import "pdfjs-dist/web/pdf_viewer.css";
import { ChevronLeft, ChevronRight, Minus, Plus, Search } from "lucide-react";
import { Button } from "./ui/button";
import { api, type Document, type ReaderInfo } from "@/lib/api";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import BookReader from "./book-reader";
import { StudyHeading, UnitHeading } from "./study-heading";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

/*
 * Continuous reading uses Mozilla's own PDF.js viewer (the engine behind
 * Firefox's reader) rather than a hand-rolled virtual list:
 *  - every page gets its real size before it is drawn, so nothing jumps;
 *  - the visible page renders first, then pages ahead in the scroll direction;
 *  - recently rendered pages stay in a bounded cache, so scrolling back is instant;
 *  - the file is streamed with HTTP range requests.
 * The viewer module reads the PDF.js API from globalThis, so it is loaded after
 * that is set.
 */
type ViewerModule = typeof import("pdfjs-dist/web/pdf_viewer.mjs");
let viewerModule: Promise<ViewerModule> | null = null;
function loadViewer() {
  (globalThis as { pdfjsLib?: unknown }).pdfjsLib = pdfjsLib;
  return (viewerModule ??= import("pdfjs-dist/web/pdf_viewer.mjs"));
}

/** Pages to draw beyond the visible ones, in the direction of scrolling. */
const RENDER_AHEAD = 3;
/** Pages never grow wider than this at 100%: a comfortable reading measure. */
const MAX_PAGE_WIDTH = 950;
const PDF_TO_CSS = 96 / 72;

// The viewer's UI strings are page labels only; ours are set explicitly, so
// the built-in translation loader (which fetches locale files) is not needed.
const l10n = {
  getLanguage: () => "en-US",
  getDirection: () => "ltr",
  get: async (_ids: unknown, _args: unknown, fallback?: string) =>
    fallback ?? "",
  translate: async () => {},
  translateOnce: async () => {},
  pause() {},
  resume() {},
  destroy: async () => {},
};

type ViewerProps = {
  doc: Document;
  info: ReaderInfo;
  page: number;
  zoom: number;
  onPage: (n: number) => void;
  onZoom: (n: number) => void;
  onSelection: (s: string) => void;
};

export default function DocumentViewer(props: ViewerProps) {
  const { doc, info, page, onPage, onSelection } = props;
  if (info.book)
    return (
      <BookReader
        book={info.book}
        doc={doc}
        page={page}
        onPage={onPage}
        onSelection={onSelection}
      />
    );
  if (doc.kind === "pdf") return <PdfViewer key={doc.id} {...props} />;
  return <TextViewer key={doc.id} {...props} />;
}

function selectionIn(holder: HTMLElement | null) {
  const selection = window.getSelection();
  const text = selection?.toString().trim();
  return text && holder?.contains(selection!.anchorNode)
    ? selection!.toString().slice(0, 3000)
    : "";
}

function ReaderToolbar({
  page,
  count,
  zoom,
  onGo,
  onZoom,
  onFind,
  searching,
  notice,
}: {
  page: number;
  count: number;
  zoom?: number;
  onGo: (n: number) => void;
  onZoom?: (n: number) => void;
  onFind: (query: string) => void;
  searching: boolean;
  notice: string;
}) {
  const [search, setSearch] = useState("");
  // The field shows what the reader types; it follows the page otherwise.
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <div className="reader-toolbar" role="toolbar" aria-label="Reader controls">
      <div className="tool-group">
        <Button
          variant="ghost"
          size="icon"
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => onGo(page - 1)}
        >
          <ChevronLeft size={17} aria-hidden />
        </Button>
        <label className="page-field">
          <span className="sr-only">Page</span>
          <input
            aria-label="Current page"
            type="number"
            inputMode="numeric"
            min={1}
            max={count}
            value={draft ?? page}
            onChange={(e) => {
              setDraft(e.target.value);
              const n = Number(e.target.value);
              if (n >= 1 && n <= count) onGo(n);
            }}
            onBlur={() => setDraft(null)}
          />
          <span className="hint">of {count}</span>
        </label>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Next page"
          disabled={page >= count}
          onClick={() => onGo(page + 1)}
        >
          <ChevronRight size={17} aria-hidden />
        </Button>
      </div>
      {zoom !== undefined && onZoom && (
        <div className="tool-group">
          <Button
            variant="ghost"
            size="icon"
            aria-label="Zoom out"
            disabled={zoom <= 0.5}
            onClick={() => onZoom(Math.max(0.5, +(zoom - 0.1).toFixed(1)))}
          >
            <Minus size={15} aria-hidden />
          </Button>
          <span className="zoom-value" aria-live="polite">
            {Math.round(zoom * 100)}%
          </span>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Zoom in"
            disabled={zoom >= 2}
            onClick={() => onZoom(Math.min(2, +(zoom + 0.1).toFixed(1)))}
          >
            <Plus size={15} aria-hidden />
          </Button>
        </div>
      )}
      <form
        className="tool-group find"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          if (search.trim()) onFind(search.trim());
        }}
      >
        <Search size={15} aria-hidden />
        <input
          type="search"
          placeholder="Find in source"
          aria-label="Find in document"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Button variant="ghost" size="sm" disabled={searching} type="submit">
          {searching ? "Searching…" : "Find"}
        </Button>
      </form>
      <span role="status" className="hint find-status">
        {notice}
      </span>
    </div>
  );
}

function PdfViewer({
  doc,
  info,
  page,
  zoom,
  onPage,
  onZoom,
  onSelection,
}: ViewerProps) {
  const container = useRef<HTMLDivElement>(null);
  const pages = useRef<HTMLDivElement>(null);
  const engine = useRef<{
    viewer: PdfViewerEngine;
    bus: PdfEventBus;
    baseWidth: number;
  } | null>(null);
  // Latest props for the engine's callbacks, which are bound once.
  const latest = useRef({ page, zoom, onPage });
  latest.current = { page, zoom, onPage };
  const reported = useRef(page);
  // A deliberate jump (outline, citation, page field) holds until the reader
  // scrolls. In a short document the target page can't reach the top, and the
  // viewer would otherwise report the page above it.
  const jumpTarget = useRef<number | null>(null);
  const lastQuery = useRef("");
  const [count, setCount] = useState(info.page_count || 1),
    [ready, setReady] = useState(false),
    [failed, setFailed] = useState(false),
    [searching, setSearching] = useState(false),
    [notice, setNotice] = useState("");

  function fitScale(state: NonNullable<typeof engine.current>) {
    const width = container.current?.clientWidth ?? 800;
    // Keep a margin of mat on both sides; never narrower than a phone column.
    const target = Math.max(240, Math.min(width - 40, MAX_PAGE_WIDTH));
    return (target * latest.current.zoom) / (state.baseWidth * PDF_TO_CSS);
  }

  useEffect(() => {
    let cancelled = false;
    const task = pdfjsLib.getDocument({ url: api.source(doc.id) });
    (async () => {
      const lib = await loadViewer();
      if (cancelled || !container.current || !pages.current) return;
      const bus = new lib.EventBus();
      const links = new lib.PDFLinkService({
        eventBus: bus,
        externalLinkTarget: lib.LinkTarget.BLANK,
        externalLinkRel: "noopener noreferrer",
      });
      const find = new lib.PDFFindController({
        eventBus: bus,
        linkService: links,
      });
      const viewer = new lib.PDFViewer({
        container: container.current,
        viewer: pages.current,
        eventBus: bus,
        linkService: links,
        findController: find,
        l10n: l10n as never,
        removePageBorders: true,
        textLayerMode: 1,
      });
      links.setViewer(viewer);

      // Read ahead like a comic reader: after the visible and next page, keep
      // drawing up to RENDER_AHEAD pages in the direction of travel.
      const queue = (viewer as unknown as { renderingQueue: RenderingQueue })
        .renderingQueue;
      const nextPage = queue.getHighestPriority.bind(queue);
      queue.getHighestPriority = (visible, views, down, ...rest) => {
        const view = nextPage(visible, views, down, ...rest);
        if (view || !visible.views.length) return view;
        const edge = down ? visible.last.id : visible.first.id;
        for (let step = 1; step <= RENDER_AHEAD; step++) {
          const ahead = views[edge - 1 + (down ? step : -step)];
          if (ahead && ahead.renderingState !== lib.RenderingStates.FINISHED)
            return ahead;
        }
        return null;
      };

      bus.on("pagesinit", () => {
        const state = engine.current;
        if (!state) return;
        viewer.currentScale = fitScale(state);
        for (let i = 0; i < viewer.pagesCount; i++)
          viewer.getPageView(i).div.id = `pdf-page-${i + 1}`;
        viewer.currentPageNumber = Math.min(
          latest.current.page,
          viewer.pagesCount,
        );
        reported.current = viewer.currentPageNumber;
        setReady(true);
      });
      bus.on("pagechanging", ({ pageNumber }: { pageNumber: number }) => {
        if (jumpTarget.current !== null && pageNumber !== jumpTarget.current)
          return;
        if (pageNumber === reported.current) return;
        reported.current = pageNumber;
        latest.current.onPage(pageNumber);
      });
      bus.on("pagerendered", ({ pageNumber, source }: PageEvent) => {
        source.div.setAttribute("aria-label", `Page ${pageNumber}`);
      });
      // The match's own page, not the viewport's: the scroll follows a moment later.
      const describe = (
        matches: { current: number; total: number },
        wrapped = false,
      ) => {
        const at = (find.selected?.pageIdx ?? -1) + 1;
        setNotice(
          `${wrapped ? "Back to the start · " : ""}Match ${matches.current || 1}${matches.total ? ` of ${matches.total}` : ""}${at > 0 ? ` · page ${at}` : ""}`,
        );
      };
      bus.on("updatefindcontrolstate", ({ state, matchesCount }: FindEvent) => {
        setSearching(state === lib.FindState.PENDING);
        if (state === lib.FindState.NOT_FOUND)
          setNotice("No matching text found.");
        else if (
          state === lib.FindState.FOUND ||
          state === lib.FindState.WRAPPED
        )
          describe(matchesCount, state === lib.FindState.WRAPPED);
      });
      // Totals keep arriving while later pages are searched.
      bus.on("updatefindmatchescount", ({ matchesCount }: FindEvent) => {
        if (matchesCount.current) describe(matchesCount);
      });

      try {
        const pdf = await task.promise;
        if (cancelled) return;
        const first = await pdf.getPage(1);
        engine.current = {
          viewer,
          bus,
          baseWidth: first.getViewport({ scale: 1 }).width,
        };
        setCount(pdf.numPages);
        viewer.setDocument(pdf);
        links.setDocument(pdf);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    // Refit when the panel is resized (tutor opened, window resized).
    const ro = new ResizeObserver(() => {
      const state = engine.current;
      if (state && state.viewer.pagesCount) {
        const next = fitScale(state);
        if (Math.abs(next - state.viewer.currentScale) > 0.005)
          state.viewer.currentScale = next;
      }
    });
    if (container.current) ro.observe(container.current);
    return () => {
      cancelled = true;
      ro.disconnect();
      engine.current?.viewer.cleanup();
      engine.current = null;
      task.destroy();
    };
    // The engine is created once per document; props reach it through `latest`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc.id]);

  // Outline, citations and the page field move the reader; scrolling reports back.
  useEffect(() => {
    const state = engine.current;
    if (!ready || !state || page === state.viewer.currentPageNumber) return;
    reported.current = page;
    jumpTarget.current = page;
    state.viewer.currentPageNumber = page;
  }, [page, ready]);

  useEffect(() => {
    const state = engine.current;
    if (ready && state) state.viewer.currentScale = fitScale(state);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoom, ready]);

  // The reader took over: follow the viewport again from here.
  function release() {
    if (jumpTarget.current === null) return;
    jumpTarget.current = null;
    requestAnimationFrame(() => {
      const current = engine.current?.viewer.currentPageNumber;
      if (current && current !== reported.current) {
        reported.current = current;
        latest.current.onPage(current);
      }
    });
  }

  function find(query: string) {
    const state = engine.current;
    if (!state) return;
    const again = query === lastQuery.current;
    lastQuery.current = query;
    jumpTarget.current = null;
    setNotice("");
    state.bus.dispatch("find", {
      source: null,
      type: again ? "again" : "",
      query,
      caseSensitive: false,
      entireWord: false,
      highlightAll: true,
      findPrevious: false,
      matchDiacritics: false,
    });
  }

  return (
    <section className="reader" aria-label="Document reader">
      <ReaderToolbar
        page={page}
        count={count}
        zoom={zoom}
        onGo={(n) => {
          const target = Math.max(1, Math.min(n, count));
          onPage(target);
        }}
        onZoom={onZoom}
        onFind={find}
        searching={searching}
        notice={notice}
      />
      <div className="pdf-stage">
        <div
          className="pdf-scroll"
          ref={container}
          tabIndex={0}
          aria-label="Pages"
          onWheel={release}
          onTouchMove={release}
          onPointerDown={release}
          onKeyDown={(event) => {
            if (/^(Arrow|Page|Home|End| )/.test(event.key)) release();
          }}
          onMouseUp={() => {
            const text = selectionIn(container.current);
            if (text) onSelection(text);
          }}
          onTouchEnd={() => {
            // Mobile selection settles after the touch ends.
            setTimeout(() => {
              const text = selectionIn(container.current);
              if (text) onSelection(text);
            }, 250);
          }}
        >
          <div ref={pages} className="pdfViewer" />
        </div>
        {!ready && !failed && (
          <div className="pdf-status" role="status">
            Opening your document…
          </div>
        )}
        {failed && (
          <div className="pdf-status">
            <p className="notice error" role="alert">
              This PDF couldn’t be displayed.{" "}
              <a href={api.source(doc.id)} target="_blank" rel="noreferrer">
                Open the original file
              </a>
            </p>
          </div>
        )}
      </div>
    </section>
  );
}

type RenderingView = { renderingState: number };
type RenderingQueue = {
  getHighestPriority: (
    visible: {
      views: { view: RenderingView }[];
      first: { id: number };
      last: { id: number };
    },
    views: RenderingView[],
    scrolledDown: boolean,
    ...rest: unknown[]
  ) => RenderingView | null;
};
type PageEvent = { pageNumber: number; source: { div: HTMLElement } };
type FindEvent = {
  state: number;
  matchesCount: { current: number; total: number };
};

const markdownId = (value: unknown) =>
  String(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "") || "section";

/** Markdown and plain-text sources: one flowing sheet, searched by excerpt. */
function TextViewer({ info, page, onPage, onSelection }: ViewerProps) {
  const holder = useRef<HTMLDivElement>(null);
  const [notice, setNotice] = useState("");
  const count = info.page_count || 1;
  return (
    <section className="reader" aria-label="Document reader">
      <ReaderToolbar
        page={page}
        count={count}
        onGo={(n) => onPage(Math.max(1, Math.min(n, count)))}
        onFind={(query) => {
          const value = query.toLowerCase();
          const chunk = info.chunks.find((c) =>
            c.text.toLowerCase().includes(value),
          );
          if (chunk) {
            document
              .getElementById(`excerpt-${chunk.chunk_index}`)
              ?.scrollIntoView({ block: "center" });
            setNotice("Matching excerpt found.");
          } else setNotice("No matching text found.");
        }}
        searching={false}
        notice={notice}
      />
      <div
        className="pdf-scroll text-scroll"
        ref={holder}
        onMouseUp={() => {
          const text = selectionIn(holder.current);
          if (text) onSelection(text);
        }}
      >
        <article className="reader-text markdown reading-prose" dir="auto">
          <Markdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: ({ href, children }) => (
                <a href={href} target="_blank" rel="noreferrer">
                  {children}
                </a>
              ),
              h1: ({ children }) => (
                <h1 id={markdownId(children)}>{children}</h1>
              ),
              h2: ({ children }) => (
                <UnitHeading id={markdownId(children)}>{children}</UnitHeading>
              ),
              h3: ({ children }) => (
                <StudyHeading id={markdownId(children)}>
                  {children}
                </StudyHeading>
              ),
            }}
          >
            {info.content || info.chunks.map((c) => c.text).join("\n\n")}
          </Markdown>
        </article>
      </div>
    </section>
  );
}
