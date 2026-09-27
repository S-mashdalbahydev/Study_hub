"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { Group, Panel, Separator, useGroupRef } from "react-resizable-panels";
import { DropdownMenu } from "radix-ui";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  MessageSquareText,
  Minimize2,
  PanelLeft,
} from "lucide-react";
import {
  api,
  type Session,
  type Document,
  type ReaderInfo,
  type Catalog,
  type Citation,
} from "@/lib/api";
import { Button } from "./ui/button";
import { ChatPanel } from "./chat-panel";
import { BookOutlineTree } from "./book-outline-tree";
import { DocumentReadiness } from "./document-readiness";
import { kindLabel, statusText } from "@/lib/labels";
const DocumentViewer = dynamic(() => import("./document-viewer"), {
  ssr: false,
  loading: () => (
    <div className="reader reader-loading" role="status">
      Opening your reader…
    </div>
  ),
});
type Layout = Session["layout"];
const VIEWS: [Layout, string, string][] = [
  ["reading", "Reading only", "The source at full width"],
  ["split", "Source and tutor", "Tutor beside the page"],
  ["chat", "Tutor only", "Conversation at full width"],
  ["focus", "Focus", "Just the page; press Esc to leave"],
];
type MobileTab = "document" | "outline" | "chat";

export function Workspace({
  documentId,
  sessionId,
  initialPage,
}: {
  documentId: string;
  sessionId?: string;
  initialPage?: number;
}) {
  const groupRef = useGroupRef();
  const [session, setSession] = useState<Session | null>(null),
    [doc, setDoc] = useState<Document | null>(null),
    [info, setInfo] = useState<ReaderInfo | null>(null),
    [models, setModels] = useState<Catalog | null>(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [selection, setSelection] = useState(""),
    [mobile, setMobile] = useState(false),
    [tab, setTab] = useState<MobileTab>("document"),
    [backToAnswer, setBackToAnswer] = useState(false),
    [outlineTab, setOutlineTab] = useState<"outline" | "figures">("outline"),
    [saving, setSaving] = useState(false);
  const queue = useRef(Promise.resolve()),
    state = useRef<Session | null>(null),
    cancelled = useRef(false),
    pendingSaves = useRef(0);
  const save = useCallback((values: Partial<Session>) => {
    if (!state.current) return;
    const sid = state.current.id;
    state.current = { ...state.current, ...values };
    setSession(state.current);
    pendingSaves.current += 1;
    setSaving(true);
    queue.current = queue.current.then(async () => {
      try {
        await api.save(sid, values);
        setError("");
      } catch (e) {
        setError(
          "Your latest change could not be saved. " + (e as Error).message,
        );
      } finally {
        pendingSaves.current -= 1;
        if (pendingSaves.current === 0) setSaving(false);
      }
    });
  }, []);
  useEffect(() => {
    cancelled.current = false;
    async function load() {
      try {
        const d = await api.document(documentId);
        const s = sessionId
          ? await api.session(sessionId)
          : await api.openSession(documentId);
        if (s.document_id !== documentId)
          throw new Error(
            "This reading session belongs to a different source. Return to your library.",
          );
        const [r, m, full] = await Promise.all([
          api.reader(documentId),
          api.models(),
          api.session(s.id),
        ]);
        if (cancelled.current) return;
        // A source never opens hidden behind the tutor; say so rather than switch silently.
        let opened =
          full.layout === "chat" ? { ...full, layout: "split" as const } : full;
        if (opened.layout !== full.layout)
          setNotice("Reopened with the source beside the tutor.");
        // Arriving from a study space means "open this chapter", not "resume".
        if (initialPage && initialPage !== opened.page)
          opened = { ...opened, page: initialPage };
        if (opened.layout !== full.layout || opened.page !== full.page)
          api
            .save(full.id, { layout: opened.layout, page: opened.page })
            .catch(() => {});
        state.current = opened;
        setSession(opened);
        setDoc(d);
        setInfo(r);
        setModels(m);
        if (!sessionId)
          history.replaceState(
            null,
            "",
            `/study/${documentId}?session=${s.id}`,
          );
      } catch (e) {
        setError((e as Error).message);
      }
    }
    load();
    const query = matchMedia("(max-width:760px)");
    const update = () => setMobile(query.matches);
    update();
    query.addEventListener("change", update);
    return () => {
      cancelled.current = true;
      query.removeEventListener("change", update);
    };
  }, [documentId, sessionId, initialPage]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 6000);
    return () => clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    if (!doc || doc.status === "ready" || doc.status === "failed") return;
    const timer = setInterval(async () => {
      try {
        const next = await api.document(documentId);
        setDoc(next);
        if (["parsed", "chunking", "embedding", "ready"].includes(next.status))
          setInfo(await api.reader(documentId));
      } catch {}
    }, 2500);
    return () => clearInterval(timer);
  }, [doc?.status, documentId]);
  useEffect(() => {
    if (!session) return;
    const mode: Layout = mobile
      ? tab === "chat"
        ? "chat"
        : "reading"
      : session.layout === "focus"
        ? "reading"
        : session.layout;
    groupRef.current?.setLayout(
      mode === "reading"
        ? { reader: 100, chat: 0 }
        : mode === "chat"
          ? { reader: 0, chat: 100 }
          : { reader: 100 - session.chat_width, chat: session.chat_width },
    );
  }, [session?.layout, mobile, tab, !!session]);
  useEffect(() => {
    if (session?.layout !== "focus") return;
    const exit = (e: KeyboardEvent) => {
      if (e.key === "Escape") save({ layout: "reading" });
    };
    addEventListener("keydown", exit);
    return () => removeEventListener("keydown", exit);
  }, [session?.layout, save]);
  function citation(c: Citation) {
    if (c.document_id !== documentId) return;
    save({
      page: c.page || session?.page || 1,
      layout: session?.layout === "chat" ? "split" : session?.layout,
    });
    if (mobile) {
      setTab("document");
      setBackToAnswer(true);
    }
    if (!c.page) {
      setTimeout(
        () =>
          document
            .getElementById(
              `excerpt-${Math.max(0, Number(c.label.replace("excerpt ", "")) - 1)}`,
            )
            ?.scrollIntoView(),
        200,
      );
    }
  }
  const go = useCallback(
    (page: number) => {
      save({ page });
      if (mobile) setTab("document");
    },
    [mobile, save],
  );
  const saveReaderPage = useCallback((page: number) => save({ page }), [save]);
  const saveReaderZoom = useCallback((zoom: number) => save({ zoom }), [save]);
  if (!session || !doc || !info)
    return (
      <main id="main" className="shell loading-page">
        <Link href="/" className="text-link">
          <ArrowLeft size={16} aria-hidden /> Library
        </Link>
        <h1>Opening your reading session…</h1>
        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : (
          <div className="skeleton" style={{ height: 360, borderRadius: 10 }} />
        )}
      </main>
    );
  const pageCount = info.book?.page_count || info.page_count;
  const focus = session.layout === "focus" && !mobile;
  const tutorOpen = mobile
    ? tab === "chat"
    : session.layout === "split" || session.layout === "chat";
  const showOutline = mobile
    ? tab === "outline"
    : session.sidebar && !focus && session.layout !== "chat";
  const sourceOutlineBook = info.outline.length
    ? {
        id: doc.id,
        title: doc.title,
        authors: [],
        filename: doc.filename,
        cover: info.cover,
        estimated_minutes: 0,
        chapter_count: info.outline.filter((x) => x.level <= 2).length,
        page_count: info.page_count || 1,
        pages: [],
        toc: info.outline.map((item, index) => ({
          id: item.id || `source-section-${index}`,
          title: item.title,
          kind:
            item.level === 1
              ? "chapter"
              : item.level === 2
                ? "section"
                : "subsection",
          level: item.level,
          parent_id: item.parent_id,
          reader_page: item.page,
        })),
      }
    : null;
  const outline = (
    <aside className="outline" aria-label="Document outline">
      {info.figures?.length > 0 && (
        <div className="outline-tabs" role="tablist" aria-label="Outline view">
          <button
            role="tab"
            aria-selected={outlineTab === "outline"}
            onClick={() => setOutlineTab("outline")}
          >
            Contents
          </button>
          <button
            role="tab"
            aria-selected={outlineTab === "figures"}
            onClick={() => setOutlineTab("figures")}
          >
            Figures · {info.figures.length}
          </button>
        </div>
      )}
      {outlineTab === "figures" && info.figures?.length ? (
        <div className="figure-index">
          {info.figures.map((figure) => (
            <button
              key={figure.id}
              aria-current={figure.page === session.page ? "true" : undefined}
              onClick={() => go(figure.page)}
            >
              <span className="figure-label">{figure.label}</span>
              <span dir="auto">{figure.caption}</span>
              <small>Page {figure.page}</small>
            </button>
          ))}
        </div>
      ) : info.book ? (
        <>
          <button
            className="cover-link"
            aria-current={session.page === 1 ? "true" : undefined}
            onClick={() => go(1)}
          >
            Cover
          </button>
          <BookOutlineTree
            book={info.book}
            page={session.page}
            onNavigate={(page) => go(page)}
          />
        </>
      ) : sourceOutlineBook ? (
        <BookOutlineTree
          book={sourceOutlineBook}
          page={session.page}
          onNavigate={(page, id) => {
            go(page);
            const item = info.outline.find(
              (x, i) => (x.id || `source-section-${i}`) === id,
            );
            if (item?.anchor)
              setTimeout(
                () =>
                  document
                    .getElementById(item.anchor!)
                    ?.scrollIntoView({ block: "start" }),
                100,
              );
          }}
        />
      ) : (
        <p className="hint outline-empty">
          {doc.status === "ready" || doc.status === "failed"
            ? "This source has no detectable headings. Use the page controls to move through it."
            : "The contents appear here once the outline is ready."}
        </p>
      )}
    </aside>
  );
  const reader = (
    <DocumentViewer
      doc={doc}
      info={info}
      page={session.page}
      zoom={session.zoom}
      onPage={saveReaderPage}
      onZoom={saveReaderZoom}
      onSelection={setSelection}
    />
  );
  const chat = (
    <ChatPanel
      session={session}
      models={models}
      selection={selection}
      onClearSelection={() => setSelection("")}
      onCitation={citation}
      onSettings={(values) => save(values)}
      onRefreshModels={() =>
        api
          .refreshModels()
          .then(setModels)
          .catch((e) => setError(e.message))
      }
      beforeSend={() => queue.current}
      ready={doc.status === "ready"}
      preparing={statusText(doc)}
      onClose={mobile ? undefined : () => save({ layout: "reading" })}
    />
  );
  const view = VIEWS.find(([value]) => value === session.layout) ?? VIEWS[1];
  return (
    <main id="main" className="workspace" data-focus={focus || undefined}>
      <header className="work-header">
        <Link
          href="/"
          className="back-link"
          aria-label="Back to library"
          title="Back to library"
        >
          <ArrowLeft size={18} aria-hidden />
          <span>Library</span>
        </Link>
        <div className="work-title">
          <h1 dir="auto">{doc.title}</h1>
          <p>
            <span>{kindLabel(doc)}</span>
            <span aria-hidden> · </span>
            <span>
              Page {session.page}
              {pageCount ? ` of ${pageCount}` : ""}
            </span>
            <span className="save-state" role="status">
              {notice ? (
                notice
              ) : saving ? (
                "Saving…"
              ) : (
                <>
                  <Check size={12} aria-hidden /> Position saved
                </>
              )}
            </span>
          </p>
        </div>
        {focus ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => save({ layout: "reading" })}
          >
            <Minimize2 size={15} aria-hidden /> Exit focus
          </Button>
        ) : (
          !mobile && (
            <div className="work-actions">
              <Button
                variant="ghost"
                size="icon"
                aria-label="Show outline"
                aria-pressed={session.sidebar}
                title="Outline"
                onClick={() => save({ sidebar: !session.sidebar })}
              >
                <PanelLeft size={17} aria-hidden />
              </Button>
              <DropdownMenu.Root>
                <DropdownMenu.Trigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`View: ${view[1]}`}
                  >
                    View <span className="hint">{view[1]}</span>
                    <ChevronDown size={14} aria-hidden />
                  </Button>
                </DropdownMenu.Trigger>
                <DropdownMenu.Portal>
                  <DropdownMenu.Content
                    className="menu"
                    align="end"
                    sideOffset={8}
                  >
                    <DropdownMenu.Label className="menu-label">
                      View
                    </DropdownMenu.Label>
                    <DropdownMenu.RadioGroup
                      value={session.layout}
                      onValueChange={(value) =>
                        save({ layout: value as Layout })
                      }
                    >
                      {VIEWS.map(([value, label, hint]) => (
                        <DropdownMenu.RadioItem
                          key={value}
                          value={value}
                          className="menu-item"
                        >
                          <span>
                            {label}
                            <small>{hint}</small>
                          </span>
                          <DropdownMenu.ItemIndicator className="menu-check">
                            <Check size={15} aria-hidden />
                          </DropdownMenu.ItemIndicator>
                        </DropdownMenu.RadioItem>
                      ))}
                    </DropdownMenu.RadioGroup>
                  </DropdownMenu.Content>
                </DropdownMenu.Portal>
              </DropdownMenu.Root>
              <Button
                variant={tutorOpen ? "secondary" : "default"}
                size="sm"
                aria-pressed={tutorOpen}
                onClick={() =>
                  save({ layout: tutorOpen ? "reading" : "split" })
                }
              >
                <MessageSquareText size={15} aria-hidden />
                Tutor
              </Button>
            </div>
          )
        )}
      </header>
      {doc.status !== "ready" && !focus && (
        <div className="reader-status-banner" role="status">
          <p>
            {doc.status === "failed" ? (
              <strong>This source couldn’t be fully prepared.</strong>
            ) : (
              <>
                <strong>You can read now.</strong> The tutor can answer
                questions grounded in this source once indexing finishes.
              </>
            )}
          </p>
          <DocumentReadiness doc={doc} />
        </div>
      )}
      {error && (
        <p role="alert" className="notice error banner">
          {error}
        </p>
      )}
      {mobile && (
        <div className="mobile-tabs" role="tablist" aria-label="Workspace">
          {(
            [
              ["document", "Read"],
              ["outline", "Outline"],
              ["chat", "Tutor"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              role="tab"
              aria-selected={tab === value}
              onClick={() => {
                setTab(value);
                setBackToAnswer(false);
              }}
            >
              {label}
              {value === "chat" && selection && (
                <span className="tab-dot" aria-label="(passage selected)" />
              )}
            </button>
          ))}
        </div>
      )}
      <div className="work-body">
        <div style={{ display: showOutline ? "contents" : "none" }}>
          {outline}
        </div>
        <Group
          groupRef={groupRef}
          orientation="horizontal"
          style={{
            display: mobile && tab === "outline" ? "none" : "flex",
            flex: 1,
          }}
          disabled={mobile || session.layout !== "split"}
          defaultLayout={{
            reader: 100 - session.chat_width,
            chat: session.chat_width,
          }}
          onLayoutChanged={(layout) => {
            if (mobile || state.current?.layout !== "split") return;
            const width = Math.round(layout.chat);
            if (
              width >= 25 &&
              width <= 70 &&
              width !== state.current?.chat_width
            )
              save({ chat_width: width });
          }}
        >
          <Panel id="reader" minSize="0%">
            {reader}
          </Panel>
          <Separator
            className="resize-handle"
            style={{
              display:
                mobile || session.layout !== "split" ? "none" : undefined,
            }}
            aria-label="Resize source and tutor"
          />
          <Panel id="chat" minSize="0%">
            {chat}
          </Panel>
        </Group>
        {mobile && tab === "document" && (selection || backToAnswer) && (
          <div className="floating-action">
            <Button
              onClick={() => {
                setTab("chat");
                setBackToAnswer(false);
              }}
            >
              <MessageSquareText size={15} aria-hidden />
              {selection && !backToAnswer
                ? "Ask tutor about selection"
                : "Back to answer"}
            </Button>
          </div>
        )}
      </div>
      {!focus && (
        <footer className="work-footer">
          <div
            className="reading-progress"
            role="progressbar"
            aria-label="Reading position"
            aria-valuemin={1}
            aria-valuemax={pageCount || 1}
            aria-valuenow={session.page}
          >
            <span
              style={{
                width: `${(session.page / (pageCount || 1)) * 100}%`,
              }}
            />
          </div>
          <span>
            Reading position · page {session.page}
            {pageCount ? ` of ${pageCount}` : ""}
          </span>
        </footer>
      )}
    </main>
  );
}
