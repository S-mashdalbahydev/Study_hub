"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  ArrowRight,
  Check,
  ChevronDown,
  ChevronRight,
  FileText,
  Layers3,
} from "lucide-react";
import {
  api,
  type LearningSession,
  type LearningTheme,
  type LessonCitation,
  type ThemeChapter,
} from "@/lib/api";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "./ui/button";
import { Confirm } from "./ui/confirm";
import { AppHeader } from "./app-header";

const KIND: Record<string, string> = { pdf: "PDF", md: "Guide", txt: "Notes" };

function pages(chapter: ThemeChapter) {
  return chapter.end_page && chapter.end_page - 1 > chapter.page
    ? `${chapter.page}–${chapter.end_page - 1}`
    : `${chapter.page}`;
}

function useTick(
  session: LearningSession,
  onUpdate: (s: LearningSession) => void,
) {
  const [busy, setBusy] = useState(false);
  return {
    busy,
    async toggle(chapter: ThemeChapter, done: boolean) {
      setBusy(true);
      try {
        onUpdate(
          await api.markChapter(
            session.id,
            chapter.theme_id,
            chapter.document_id,
            chapter.chapter_id,
            done,
          ),
        );
      } finally {
        setBusy(false);
      }
    },
  };
}

function Checklist({
  session,
  selected,
  onSelect,
  onUpdate,
}: {
  session: LearningSession;
  selected: string | null;
  onSelect: (id: string) => void;
  onUpdate: (s: LearningSession) => void;
}) {
  const [open, setOpen] = useState<Set<string>>(
    () => new Set(selected ? [selected] : []),
  );
  const { busy, toggle } = useTick(session, onUpdate);
  return (
    <div className="map-tree" role="tree" aria-label="Topics">
      {session.themes.map((theme) => {
        const expanded = open.has(theme.id);
        const done = theme.chapters.filter((c) => c.done).length;
        return (
          <div
            key={theme.id}
            role="treeitem"
            aria-expanded={expanded}
            aria-selected={selected === theme.id}
            className={`map-tree-theme ${selected === theme.id ? "active" : ""}`}
          >
            <div className="map-tree-row">
              <button
                type="button"
                className="tree-disclosure"
                aria-label={`${expanded ? "Collapse" : "Expand"} ${theme.title}`}
                onClick={() =>
                  setOpen((current) => {
                    const next = new Set(current);
                    if (next.has(theme.id)) next.delete(theme.id);
                    else next.add(theme.id);
                    return next;
                  })
                }
              >
                {expanded ? (
                  <ChevronDown size={14} aria-hidden />
                ) : (
                  <ChevronRight size={14} aria-hidden />
                )}
              </button>
              <button
                type="button"
                className="map-tree-label"
                onClick={() => {
                  onSelect(theme.id);
                  setOpen((current) => new Set(current).add(theme.id));
                }}
              >
                <span dir="auto">{theme.title}</span>
                <span className="map-tree-count">
                  {done}/{theme.chapters.length}
                  <span className="sr-only"> chapters read</span>
                </span>
              </button>
            </div>
            {expanded && (
              <div role="group">
                {theme.chapters.map((chapter) => (
                  <label
                    key={`${chapter.document_id}-${chapter.chapter_id}`}
                    className={`map-tree-chapter ${chapter.done ? "done" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={chapter.done}
                      disabled={busy}
                      onChange={(e) => toggle(chapter, e.target.checked)}
                    />
                    <span className="map-tree-chapter-title" dir="auto">
                      {chapter.title}
                    </span>
                    <span className="map-tree-page">p. {chapter.page}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ThemeDetail({
  theme,
  session,
  onUpdate,
}: {
  theme: LearningTheme;
  session: LearningSession;
  onUpdate: (s: LearningSession) => void;
}) {
  const { busy, toggle } = useTick(session, onUpdate);
  const books = [...new Set(theme.chapters.map((c) => c.document_id))];
  const done = theme.chapters.filter((c) => c.done).length;
  return (
    <section className="map-theme" aria-labelledby="theme-title">
      <header>
        <p className="kind">Topic</p>
        <h2 id="theme-title" dir="auto">
          {theme.title}
        </h2>
        {theme.summary && (
          <p className="map-summary" dir="auto">
            {theme.summary}
          </p>
        )}
        <div className="progress-line">
          <span className="meter" aria-hidden>
            <span
              style={{
                width: `${theme.chapters.length ? (done / theme.chapters.length) * 100 : 0}%`,
              }}
            />
          </span>
          <span>
            {done} of {theme.chapters.length} chapters read
          </span>
        </div>
      </header>
      <h3 className="subheading">Chapters to read</h3>
      {books.map((id) => {
        const rows = theme.chapters.filter((c) => c.document_id === id);
        return (
          <div className="map-source" key={id}>
            <p className="map-source-name">
              <span className="kind">
                {KIND[rows[0].kind] || rows[0].kind.toUpperCase()}
              </span>
              <span dir="auto">{rows[0].book}</span>
            </p>
            <ul>
              {rows.map((chapter) => (
                <li
                  className={`map-chapter ${chapter.done ? "done" : ""}`}
                  key={chapter.chapter_id}
                >
                  <input
                    type="checkbox"
                    checked={chapter.done}
                    disabled={busy}
                    aria-label={`Mark ${chapter.title} as read`}
                    onChange={(e) => toggle(chapter, e.target.checked)}
                  />
                  <Link
                    className="map-open"
                    href={`/study/${chapter.document_id}?page=${chapter.page}`}
                  >
                    <span className="map-chapter-title" dir="auto">
                      {chapter.title}
                    </span>
                    <span className="map-pages">p. {pages(chapter)}</span>
                    <ArrowRight size={14} aria-hidden />
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
      <Lesson theme={theme} session={session} onUpdate={onUpdate} />
    </section>
  );
}

function linked(lesson: string, citations: LessonCitation[]) {
  // Citations are built server-side; this only turns [S1] into a link to that page.
  return citations.reduce(
    (text, c) => text.replaceAll(`[S${c.n}]`, `[S${c.n}](${c.url})`),
    lesson,
  );
}

function Lesson({
  theme,
  session,
  onUpdate,
}: {
  theme: LearningTheme;
  session: LearningSession;
  onUpdate: (s: LearningSession) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [answer, setAnswer] = useState("");
  const [feedback, setFeedback] = useState("");
  const [marking, setMarking] = useState(false);
  if (!theme.lesson)
    return (
      <div className="lesson-start">
        <h3 className="subheading">Lesson</h3>
        <p className="hint">
          A lesson written only from the chapters above, with a citation for
          every claim and a short exercise.
        </p>
        <Button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              onUpdate(await api.teachTheme(session.id, theme.id));
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Reading the chapters…" : "Write the lesson"}
        </Button>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </div>
    );
  return (
    <div className="lesson">
      <h3 className="subheading">Lesson</h3>
      <div className="markdown reading-prose" dir="auto">
        <Markdown remarkPlugins={[remarkGfm]}>
          {linked(theme.lesson, theme.citations)}
        </Markdown>
      </div>
      <p className="subheading small">Sources used</p>
      <ul className="lesson-sources">
        {theme.citations.map((c) => (
          <li key={c.n}>
            <Link href={c.url} className="source-citation">
              <span className="citation-n">S{c.n}</span>
              <span dir="auto">{c.title}</span>
              {c.page ? <span className="hint">p. {c.page}</span> : null}
            </Link>
          </li>
        ))}
      </ul>
      {theme.exercise && (
        <div className="lesson-practice">
          <p className="section-kicker">Exercise</p>
          <label className="field">
            Your answer
            <textarea
              rows={5}
              placeholder="Write your answer…"
              value={answer}
              dir="auto"
              onChange={(e) => setAnswer(e.target.value)}
            />
          </label>
          <div className="dialog-actions start">
            <Button
              disabled={!answer.trim() || marking}
              onClick={async () => {
                setMarking(true);
                setError("");
                try {
                  const result = await api.answerTheme(
                    session.id,
                    theme.id,
                    answer,
                  );
                  setFeedback(result.feedback);
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setMarking(false);
                }
              }}
            >
              {marking ? "Checking…" : "Check my answer"}
            </Button>
          </div>
          {feedback && (
            <div className="lesson-feedback markdown" role="status" dir="auto">
              <p className="section-kicker">Feedback</p>
              <Markdown remarkPlugins={[remarkGfm]}>{feedback}</Markdown>
            </div>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function ThemeEditor({
  session,
  onUpdate,
}: {
  session: LearningSession;
  onUpdate: (s: LearningSession) => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(
    session.themes.map((t) => t.title).join("\n"),
  );
  const read = session.themes
    .flatMap((t) => t.chapters)
    .filter((c) => c.done).length;
  const lessons = session.themes.filter((t) => t.lesson).length;
  if (!open)
    return (
      <Button variant="ghost" size="sm" onClick={() => setOpen(true)}>
        Change topics
      </Button>
    );
  return (
    <div className="map-editor">
      <label className="field" htmlFor="topics">
        Topics, one per line
        <textarea
          id="topics"
          rows={6}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={"Scaling under load\nValidating an idea\nHandling failure"}
          dir="auto"
        />
        <small>Leave it empty to let the tutor suggest topics from your goal.</small>
      </label>
      <p className="hint">
        Rebuilding regroups your sources’ chapters under these topics. Your
        sources and goal stay. Current topics, their{" "}
        <strong>{lessons} written {lessons === 1 ? "lesson" : "lessons"}</strong>{" "}
        and <strong>{read} read {read === 1 ? "tick" : "ticks"}</strong> are
        replaced.
      </p>
      <div className="dialog-actions start">
        <Button variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
        <Confirm
          trigger={<Button>Rebuild topic map</Button>}
          title="Rebuild the topic map?"
          description={`This replaces ${session.themes.length} topics, ${lessons} lessons and ${read} read ticks in “${session.title}”. Your sources and goal are kept. This can’t be undone.`}
          confirmLabel="Rebuild"
          onConfirm={async () => {
            onUpdate(
              await api.generateNotebook(
                session.id,
                text
                  .split("\n")
                  .map((line) => line.trim())
                  .filter(Boolean),
              ),
            );
            setOpen(false);
          }}
        />
      </div>
    </div>
  );
}

export function StudySessionView({ id }: { id: string }) {
  const [session, setSession] = useState<LearningSession | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api
      .learningSession(id)
      .then((result) => {
        setSession(result);
        setSelected(result.themes[0]?.id ?? null);
      })
      .catch((e) => setError(e.message));
  }, [id]);
  if (!session)
    return (
      <div className="app">
        <AppHeader />
        <main id="main" className="shell">
          <p className="notice" role={error ? "alert" : "status"}>
            {error || "Opening your study space…"}
          </p>
        </main>
      </div>
    );
  const chapters = session.themes.flatMap((t) => t.chapters);
  const read = chapters.filter((c) => c.done).length;
  const current =
    session.themes.find((t) => t.id === selected) ?? session.themes[0];
  return (
    <div className="app">
      <AppHeader />
      <main id="main" className="shell notebook-shell">
        <nav aria-label="Breadcrumb" className="breadcrumb">
          <Link href="/spaces">Study spaces</Link>
          <ChevronRight size={14} aria-hidden />
          <span aria-current="page" dir="auto">
            {session.title}
          </span>
        </nav>
        <header className="notebook-header">
          <h1 dir="auto">{session.title}</h1>
          <p className="goal" dir="auto">
            {session.learning_goal}
          </p>
          {chapters.length > 0 && (
            <p>
              {read} of {chapters.length} chapters read across{" "}
              {session.materials.length} sources
            </p>
          )}
        </header>
        <div className="notebook-layout">
          <aside className="materials-panel" aria-label="Sources and topics">
            <h2 className="subheading">Sources</h2>
            <ul className="materials-list">
              {session.materials.map((m) => (
                <li key={m.document_id}>
                  <Link href={`/study/${m.document_id}`}>
                    <FileText size={15} aria-hidden />
                    <span dir="auto">
                      {m.title}
                      <small>{KIND[m.kind] || m.kind.toUpperCase()}</small>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
            {session.themes.length > 0 && (
              <>
                <h2 className="subheading">Topics</h2>
                <Checklist
                  session={session}
                  selected={current?.id ?? null}
                  onSelect={setSelected}
                  onUpdate={setSession}
                />
              </>
            )}
          </aside>
          <article className="teaching-notebook">
            {session.themes.length && current ? (
              <>
                <div className="map-toolbar">
                  <ThemeEditor session={session} onUpdate={setSession} />
                </div>
                <ThemeDetail
                  theme={current}
                  session={session}
                  onUpdate={setSession}
                />
                {read > 0 && read === chapters.length && (
                  <p className="map-complete" role="status">
                    <Check size={16} aria-hidden /> Every chapter in this space
                    is marked read.
                  </p>
                )}
              </>
            ) : (
              <section className="empty-state">
                <Layers3 size={32} strokeWidth={1.4} aria-hidden />
                <h2>Build a topic map.</h2>
                <p>
                  StudyHub groups the real chapters of every source you picked
                  under the topics in your goal, so you can work through them
                  together.
                </p>
                <Button
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    setError("");
                    try {
                      const result = await api.generateNotebook(id);
                      setSession(result);
                      setSelected(result.themes[0]?.id ?? null);
                    } catch (e) {
                      setError((e as Error).message);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  {busy ? "Reading your sources…" : "Build topic map"}
                </Button>
                {error && (
                  <p className="error" role="alert">
                    {error}
                  </p>
                )}
              </section>
            )}
          </article>
        </div>
      </main>
    </div>
  );
}
