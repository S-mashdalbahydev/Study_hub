"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { DropdownMenu } from "radix-ui";
import {
  ArrowRight,
  BookOpen,
  ChevronDown,
  FileText,
  Layers3,
  Link as LinkIcon,
  Plus,
  RefreshCw,
  Search,
  Upload,
  X,
} from "lucide-react";
import {
  api,
  type Document,
  type LearningSession,
  type ResearchJob,
  type Session,
} from "@/lib/api";
import { isGuide, kindLabel, shortDate, statusText } from "@/lib/labels";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { AppHeader } from "./app-header";
import { DocumentReadiness } from "./document-readiness";
import {
  ACTIVE_RESEARCH,
  CreateFlow,
  resumable,
  type CreateKind,
} from "./research";

export function DocumentCover({ doc }: { doc: Document }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="cover" data-guide={isGuide(doc) || undefined}>
      {doc.status === "ready" && !failed ? (
        <img
          src={api.cover(doc.id)}
          alt=""
          loading="lazy"
          onError={() => setFailed(true)}
        />
      ) : (
        <div className="cover-placeholder">
          <FileText size={30} strokeWidth={1.4} aria-hidden />
          <span dir="auto">{doc.title}</span>
        </div>
      )}
    </div>
  );
}
const canOpen = (doc: Document) =>
  doc.status !== "failed" && (doc.kind === "pdf" || doc.status === "ready");
type Filter = "all" | "sources" | "guides";
type SpaceSummary = Pick<
  LearningSession,
  "id" | "title" | "learning_goal" | "updated_at"
>;

export function Library() {
  const router = useRouter();
  const [docs, setDocs] = useState<Document[]>([]),
    [sessions, setSessions] = useState<Session[]>([]),
    [spaces, setSpaces] = useState<SpaceSummary[]>([]),
    [jobs, setJobs] = useState<ResearchJob[]>([]),
    [loading, setLoading] = useState(true),
    [query, setQuery] = useState(""),
    [filter, setFilter] = useState<Filter>("all"),
    [error, setError] = useState(""),
    [detail, setDetail] = useState<Document | null>(null),
    [busy, setBusy] = useState(false),
    [spaceOpen, setSpaceOpen] = useState(false),
    [spaceTitle, setSpaceTitle] = useState(""),
    [spaceGoal, setSpaceGoal] = useState(""),
    [selectedDocs, setSelectedDocs] = useState<string[]>([]),
    [create, setCreate] = useState<{
      open: boolean;
      kind: CreateKind;
      jobId: string | null;
    }>({ open: false, kind: "topic", jobId: null }),
    [drag, setDrag] = useState(false),
    [uploads, setUploads] = useState<
      { name: string; percent: number; error?: string }[]
    >([]);
  const input = useRef<HTMLInputElement>(null),
    depth = useRef(0);
  const load = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([api.documents(), api.sessions()]);
      setDocs(d);
      setSessions(s);
      // Spaces and research are secondary; a failure there shouldn't blank the library.
      api
        .learningSessions()
        .then(setSpaces)
        .catch(() => {});
      api
        .research()
        .then((all) => setJobs(all.filter(resumable)))
        .catch(() => {});
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    load();
    const refreshVisiblePage = () => {
      if (document.visibilityState === "visible") load();
    };
    addEventListener("focus", refreshVisiblePage);
    document.addEventListener("visibilitychange", refreshVisiblePage);
    return () => {
      removeEventListener("focus", refreshVisiblePage);
      document.removeEventListener("visibilitychange", refreshVisiblePage);
    };
  }, [load]);
  const hasLiveWork =
    docs.some((doc) => !["ready", "failed"].includes(doc.status)) ||
    jobs.some((job) => ACTIVE_RESEARCH.has(job.status));
  useEffect(() => {
    if (!hasLiveWork) return;
    const timer = setInterval(load, 2500);
    return () => clearInterval(timer);
  }, [hasLiveWork, load]);
  async function upload(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    setError("");
    setUploads(list.map((f) => ({ name: f.name, percent: 0 })));
    for (const [i, file] of list.entries()) {
      try {
        await api.upload(file, (p) =>
          setUploads((old) =>
            old.map((x, j) => (j === i ? { ...x, percent: p } : x)),
          ),
        );
      } catch (e) {
        setUploads((old) =>
          old.map((x, j) =>
            j === i ? { ...x, error: (e as Error).message } : x,
          ),
        );
      }
      await load();
    }
  }
  async function open(doc: Document | string, fresh = false) {
    const id = typeof doc === "string" ? doc : doc.id;
    setBusy(true);
    setError("");
    try {
      const s = await api.openSession(id, fresh);
      router.push(`/study/${id}?session=${s.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  async function rescan() {
    setBusy(true);
    try {
      const r = await api.refresh();
      setError(r.warnings.join("\n"));
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const startCreate = (kind: CreateKind) =>
    setCreate({ open: true, kind, jobId: null });
  const latest = new Map<string, Session>();
  for (const s of sessions)
    if (!latest.has(s.document_id)) latest.set(s.document_id, s);
  const byId = new Map(docs.map((d) => [d.id, d]));
  const continuing = [...latest.values()]
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .map((s) => ({ session: s, doc: byId.get(s.document_id) }))
    .filter(
      (x): x is { session: Session; doc: Document } =>
        !!x.doc && x.doc.status !== "failed",
    )
    .slice(0, 4);
  const collection = docs.filter(
    (d) =>
      d.title.toLowerCase().includes(query.trim().toLowerCase()) &&
      (filter === "all" || (filter === "guides") === isGuide(d)),
  );
  const addMenu = (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <Button>
          <Plus size={16} aria-hidden />
          Add or create
          <ChevronDown size={14} aria-hidden />
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="menu" align="end" sideOffset={8}>
          <DropdownMenu.Item
            className="menu-item"
            onSelect={() => input.current?.click()}
          >
            <Upload size={16} aria-hidden />
            <span>
              Upload a source
              <small>PDF, Markdown, text or DOCX</small>
            </span>
          </DropdownMenu.Item>
          <DropdownMenu.Item
            className="menu-item"
            onSelect={() => startCreate("topic")}
          >
            <Search size={16} aria-hidden />
            <span>
              Research a topic or URLs
              <small>A cited guide you review first</small>
            </span>
          </DropdownMenu.Item>
          <DropdownMenu.Item
            className="menu-item"
            onSelect={() => startCreate("companion")}
          >
            <BookOpen size={16} aria-hidden />
            <span>
              Make a guide from a source
              <small>Explanations and exercises per chapter</small>
            </span>
          </DropdownMenu.Item>
          <DropdownMenu.Separator className="menu-separator" />
          <DropdownMenu.Item
            className="menu-item"
            onSelect={() => setSpaceOpen(true)}
          >
            <Layers3 size={16} aria-hidden />
            <span>
              New study space
              <small>Learn across two or more sources</small>
            </span>
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
  return (
    <div
      className="app"
      onDragEnter={(e) => {
        if (e.dataTransfer.types.includes("Files")) {
          e.preventDefault();
          depth.current++;
          setDrag(true);
        }
      }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={() => {
        if (--depth.current <= 0) setDrag(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        depth.current = 0;
        setDrag(false);
        upload(e.dataTransfer.files);
      }}
    >
      <AppHeader action={addMenu} />
      <main id="main" className="shell">
        <input
          ref={input}
          type="file"
          multiple
          accept=".pdf,.md,.txt,.docx"
          hidden
          aria-label="Upload documents"
          onChange={(e) => {
            if (e.target.files) upload(e.target.files);
            e.target.value = "";
          }}
        />
        <section className="page-intro">
          <h1>Your library</h1>
          <p>
            Read your sources, ask the tutor about any page, and turn what you
            read into guides and study spaces you can come back to.
          </p>
        </section>
        {error && (
          <p role="alert" className="notice error">
            {error}
          </p>
        )}
        {uploads.length > 0 && (
          <ul className="uploads" aria-label="Uploads">
            {uploads.map((u, i) => (
              <li className="upload-item" role="status" key={i}>
                <Upload size={16} aria-hidden />
                <span className="upload-name">{u.name}</span>
                {u.error ? (
                  <span className="error">{u.error}</span>
                ) : (
                  <>
                    <progress
                      max={100}
                      value={u.percent}
                      aria-label={`Uploading ${u.name}`}
                    />
                    <span className="hint">
                      {u.percent === 100 ? "Upload complete" : `${u.percent}%`}
                    </span>
                  </>
                )}
              </li>
            ))}
            <li>
              <button
                className="text-button"
                onClick={() => setUploads([])}
                aria-label="Dismiss upload list"
              >
                <X size={14} aria-hidden /> Dismiss
              </button>
            </li>
          </ul>
        )}
        {jobs.length > 0 && (
          <section className="jobs" aria-label="Study guides in progress">
            {jobs.map((job) => (
              <div className="job" key={job.id}>
                <span className="job-state">
                  {job.status === "review" ? "Ready for review" : "Researching"}
                </span>
                <span className="job-title" dir="auto">
                  {job.query}
                </span>
                <Button
                  size="sm"
                  variant={job.status === "review" ? "default" : "outline"}
                  aria-label={`Open research: ${job.query}`}
                  onClick={() =>
                    setCreate({ open: true, kind: "topic", jobId: job.id })
                  }
                >
                  {job.status === "review" ? "Review" : "View progress"}
                </Button>
              </div>
            ))}
          </section>
        )}
        {loading ? (
          <div className="grid" aria-label="Loading library" aria-busy>
            {[0, 1, 2, 3].map((n) => (
              <div key={n} className="doc-card">
                <div className="cover skeleton" />
              </div>
            ))}
          </div>
        ) : !docs.length ? (
          <section className="empty-state">
            <BookOpen size={32} strokeWidth={1.4} aria-hidden />
            <h2>Add a book, paper, or document to start reading.</h2>
            <p>
              Or start from a question: StudyHub can research a topic and draft
              a cited guide for you to review.
            </p>
            <div className="actions">
              <Button onClick={() => input.current?.click()}>
                <Upload size={16} aria-hidden /> Upload a source
              </Button>
              <Button variant="outline" onClick={() => startCreate("topic")}>
                <Search size={16} aria-hidden /> Research a topic
              </Button>
            </div>
            <p className="hint">
              You can also drop files anywhere on this page.
            </p>
          </section>
        ) : (
          <>
            {continuing.length > 0 && (
              <section aria-labelledby="continue-heading">
                <div className="section-heading">
                  <h2 id="continue-heading">Continue reading</h2>
                </div>
                <ul className="continue-list">
                  {continuing.map(({ doc, session }) => (
                    <li key={doc.id}>
                      <button
                        className="continue-item"
                        onClick={() => open(doc)}
                        aria-label={`Continue ${doc.title}`}
                        disabled={busy}
                      >
                        <DocumentCover doc={doc} />
                        <span className="continue-text">
                          <span className="kind">{kindLabel(doc)}</span>
                          <strong dir="auto">{doc.title}</strong>
                          <span className="meta">
                            Page {session.page} · last read{" "}
                            {shortDate(session.updated_at)}
                          </span>
                          <span className="continue-cta" aria-hidden>
                            Continue <ArrowRight size={14} />
                          </span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            <section aria-labelledby="spaces-heading">
              <div className="section-heading">
                <h2 id="spaces-heading">Study spaces</h2>
                {spaces.length > 0 && (
                  <Link href="/spaces" className="text-link">
                    View all {spaces.length}{" "}
                    <ArrowRight size={14} aria-hidden />
                  </Link>
                )}
              </div>
              {spaces.length ? (
                <ul className="space-list">
                  {spaces.slice(0, 3).map((space) => (
                    <li key={space.id}>
                      <Link
                        href={`/sessions/${space.id}`}
                        className="space-card"
                      >
                        <strong dir="auto">{space.title}</strong>
                        <span dir="auto">{space.learning_goal}</span>
                        <small>Last active {shortDate(space.updated_at)}</small>
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="inline-empty">
                  <p>
                    A study space groups two or more sources around one goal,
                    with a topic map, lessons and progress.
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setSpaceOpen(true)}
                  >
                    <Layers3 size={15} aria-hidden /> New study space
                  </Button>
                </div>
              )}
            </section>
            <section id="library" aria-labelledby="collection-heading">
              <div className="section-heading">
                <h2 id="collection-heading">Sources and guides</h2>
                <span className="hint">
                  {collection.length} of {docs.length}
                </span>
              </div>
              <div className="collection-tools">
                <label className="search">
                  <Search size={16} aria-hidden />
                  <input
                    type="search"
                    aria-label="Search library"
                    placeholder="Search by title"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </label>
                <div className="segmented" role="radiogroup" aria-label="Show">
                  {(
                    [
                      ["all", "All"],
                      ["sources", "Sources"],
                      ["guides", "Study guides"],
                    ] as const
                  ).map(([value, label]) => (
                    <button
                      key={value}
                      role="radio"
                      aria-checked={filter === value}
                      onClick={() => setFilter(value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              {collection.length ? (
                <ul className="grid">
                  {collection.map((doc) => {
                    const session = latest.get(doc.id);
                    const failed = doc.status === "failed";
                    return (
                      <li
                        className="doc-card"
                        key={doc.id}
                        data-failed={failed || undefined}
                      >
                        <button
                          className="doc-open"
                          onClick={() =>
                            canOpen(doc) ? open(doc) : setDetail(doc)
                          }
                          aria-label={`${failed ? "Details for" : session ? "Continue" : "Open"} ${doc.title}`}
                        >
                          <DocumentCover doc={doc} />
                          <span className="doc-affordance" aria-hidden>
                            {failed
                              ? "Details"
                              : session
                                ? `Continue · p. ${session.page}`
                                : "Open"}
                          </span>
                        </button>
                        <span className="kind">{kindLabel(doc)}</span>
                        <h3 dir="auto">{doc.title}</h3>
                        <p className="meta" data-state={doc.status}>
                          {failed
                            ? statusText(doc)
                            : doc.status !== "ready"
                              ? `${statusText(doc)}…${doc.kind === "pdf" ? " You can read it now." : ""}`
                              : session
                                ? `Last read ${shortDate(session.updated_at)}`
                                : `Added ${shortDate(doc.created_at)}`}
                        </p>
                        {failed && (
                          <div className="card-actions">
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={busy}
                              onClick={rescan}
                            >
                              <RefreshCw size={13} aria-hidden /> Refresh library
                            </Button>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <div className="inline-empty">
                  <p>
                    {query
                      ? `Nothing matches “${query}”.`
                      : filter === "guides"
                        ? "No study guides yet."
                        : "No sources yet."}
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setQuery("");
                      setFilter("all");
                    }}
                  >
                    Clear search and filters
                  </Button>
                </div>
              )}
            </section>
          </>
        )}
        <footer className="page-footer">
          <span>Drop files anywhere on this page to add them.</span>
          <Button variant="ghost" size="sm" disabled={busy} onClick={rescan}>
            <RefreshCw size={14} aria-hidden />
            Refresh library
          </Button>
        </footer>
      </main>
      {drag && (
        <div className="drop-overlay" aria-hidden>
          <Upload size={40} strokeWidth={1.4} />
          <p className="drop-title">Drop to add to your library</p>
          <p>PDF, Markdown, text or DOCX</p>
        </div>
      )}
      <CreateFlow
        open={create.open}
        onOpenChange={(value) => setCreate((c) => ({ ...c, open: value }))}
        kind={create.kind}
        onKindChange={(kind) => setCreate((c) => ({ ...c, kind }))}
        jobId={create.jobId}
        documents={docs}
        onPublished={load}
        onOpenDocument={(id) => open(id)}
      />
      <Dialog open={spaceOpen} onOpenChange={setSpaceOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New study space</DialogTitle>
            <DialogDescription>
              Pick at least two sources or guides. StudyHub maps their real
              chapters to the topics in your goal, and cites each one.
            </DialogDescription>
          </DialogHeader>
          <label className="field">
            Name
            <input
              value={spaceTitle}
              onChange={(e) => setSpaceTitle(e.target.value)}
              placeholder="Distributed systems foundations"
              dir="auto"
            />
          </label>
          <label className="field">
            Goal
            <textarea
              rows={2}
              value={spaceGoal}
              onChange={(e) => setSpaceGoal(e.target.value)}
              placeholder="Understand how these sources explain reliability"
              dir="auto"
            />
          </label>
          <fieldset className="material-picker">
            <legend>Sources · {selectedDocs.length} selected</legend>
            {docs
              .filter((d) => d.status === "ready")
              .map((d) => (
                <label key={d.id}>
                  <input
                    type="checkbox"
                    checked={selectedDocs.includes(d.id)}
                    onChange={(e) =>
                      setSelectedDocs((old) =>
                        e.target.checked
                          ? [...old, d.id]
                          : old.filter((id) => id !== d.id),
                      )
                    }
                  />
                  <span dir="auto">
                    {d.title}
                    <small>{kindLabel(d)}</small>
                  </span>
                </label>
              ))}
          </fieldset>
          <div className="dialog-actions">
            <Button
              disabled={
                busy ||
                !spaceTitle.trim() ||
                !spaceGoal.trim() ||
                selectedDocs.length < 2
              }
              onClick={async () => {
                setBusy(true);
                try {
                  const s = await api.createLearningSession({
                    title: spaceTitle,
                    learning_goal: spaceGoal,
                    document_ids: selectedDocs,
                  });
                  router.push(`/sessions/${s.id}`);
                } catch (e) {
                  setError((e as Error).message);
                  setBusy(false);
                }
              }}
            >
              Create study space
            </Button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog
        open={!!detail}
        onOpenChange={(v) => {
          if (!v) setDetail(null);
        }}
      >
        <DialogContent className="detail-dialog">
          <DialogHeader>
            <DialogTitle dir="auto">{detail?.title}</DialogTitle>
            <DialogDescription>
              {detail && `${kindLabel(detail)} · ${statusText(detail)}`}
            </DialogDescription>
          </DialogHeader>
          {detail && (
            <div className="detail-body">
              <DocumentCover doc={detail} />
              <div>
                <DocumentReadiness doc={detail} />
                {detail.status === "failed" ? (
                  <>
                    {detail.error && (
                      <details className="error-details">
                        <summary>What went wrong</summary>
                        <p className="error">{detail.error}</p>
                      </details>
                    )}
                    <p className="hint">
                      Refreshing retries every document that couldn’t be
                      prepared, including this one, and re-indexes any that need it.
                    </p>
                    <div className="dialog-actions">
                      <Button onClick={rescan} disabled={busy}>
                        <RefreshCw size={14} aria-hidden /> Refresh library
                      </Button>
                    </div>
                  </>
                ) : (
                  <>
                    <p className="hint">
                      {canOpen(detail)
                        ? "Your place and conversation are saved as you read."
                        : "This item can be opened once its text has been read. It usually takes under a minute."}
                    </p>
                    <div className="dialog-actions">
                      {latest.has(detail.id) && (
                        <Button
                          variant="ghost"
                          disabled={busy}
                          onClick={() => open(detail, true)}
                        >
                          Start a new reading session
                        </Button>
                      )}
                      <Button
                        disabled={busy || !canOpen(detail)}
                        onClick={() => open(detail)}
                      >
                        {latest.has(detail.id) ? "Continue reading" : "Open"}
                      </Button>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
