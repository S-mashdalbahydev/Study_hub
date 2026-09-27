"use client";
import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  BookOpen,
  Check,
  ExternalLink,
  FileText,
  Globe2,
  Link as LinkIcon,
  Search,
} from "lucide-react";
import { api, type Document, type ResearchJob } from "@/lib/api";
import { Button } from "./ui/button";
import { Confirm } from "./ui/confirm";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { StudyHeading, UnitHeading } from "./study-heading";

export type CreateKind = "companion" | "topic" | "url";
const MAX_SELECTED_RESOURCES = 4;
const CATEGORY: Record<
  ResearchJob["resources"][number]["resource_type"],
  string
> = {
  video: "Videos",
  playlist_en: "English playlists",
  playlist_ar: "Arabic playlists",
  article_or_demo: "Articles",
};
export const ACTIVE_RESEARCH = new Set([
  "created",
  "planning",
  "searching",
  "reading_sources",
  "checking_coverage",
  "synthesizing",
  "publishing",
  "indexing",
]);
export const resumable = (job: ResearchJob) =>
  ACTIVE_RESEARCH.has(job.status) || job.status === "review";
const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};
const KINDS: [CreateKind, string, string, typeof BookOpen][] = [
  [
    "companion",
    "Guide from a source",
    "Explain a document already in your library, chapter by chapter.",
    BookOpen,
  ],
  [
    "topic",
    "Research a topic",
    "Search the web, keep reliable pages, and write a cited guide.",
    Search,
  ],
  [
    "url",
    "Research URLs",
    "Turn specific public web pages into a cited guide.",
    LinkIcon,
  ],
];

function unitOrder(job: ResearchJob) {
  const titles = job.outline.map((line) =>
    line.replace(/^Unit\s+[\d,\s]+:\s*/, "").trim(),
  );
  for (const r of job.resources)
    if (!titles.includes(r.chapter)) titles.push(r.chapter);
  return titles.filter((title, i) => titles.indexOf(title) === i);
}

/** Per-unit resource choice, shown inline beside the draft (no stacked dialog). */
function ResourceCurator({
  job,
  onUpdated,
}: {
  job: ResearchJob;
  onUpdated: (job: ResearchJob) => void;
}) {
  const [open, setOpen] = useState<string | null>(null),
    [busyId, setBusyId] = useState<string | null>(null),
    [error, setError] = useState("");
  async function toggle(id: string, selected: boolean) {
    setBusyId(id);
    setError("");
    try {
      onUpdated(await api.selectResource(job.id, id, selected));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }
  return (
    <section className="review-section" aria-labelledby="resources-heading">
      <h3 id="resources-heading">Videos and articles</h3>
      <p className="hint">
        Optional. Choose up to {MAX_SELECTED_RESOURCES} per kind for each unit.
        StudyHub found these by title and address; it has not watched them.
      </p>
      {unitOrder(job).map((chapter) => (
        <div key={chapter} className="resource-unit-row">
          <span className="resource-unit-title">{chapter}</span>
          <div className="resource-chip-row">
            {(Object.keys(CATEGORY) as (keyof typeof CATEGORY)[]).map(
              (type) => {
                const candidates = job.resources.filter(
                  (r) => r.chapter === chapter && r.resource_type === type,
                );
                const selected = candidates.filter((c) => c.selected).length;
                const key = `${chapter}::${type}`;
                return (
                  <button
                    key={type}
                    type="button"
                    className="resource-chip"
                    aria-expanded={open === key}
                    disabled={!candidates.length}
                    onClick={() => setOpen(open === key ? null : key)}
                  >
                    {CATEGORY[type]}
                    <span className="resource-chip-count">
                      {candidates.length
                        ? `${selected}/${Math.min(candidates.length, MAX_SELECTED_RESOURCES)}`
                        : "none found"}
                    </span>
                  </button>
                );
              },
            )}
          </div>
          {open?.startsWith(chapter + "::") && (
            <ul className="resource-picker-list">
              {job.resources
                .filter(
                  (r) =>
                    r.chapter === chapter &&
                    r.resource_type === open.split("::")[1],
                )
                .map((candidate, _, all) => {
                  const count = all.filter((c) => c.selected).length;
                  return (
                    <li key={candidate.id}>
                      <label>
                        <input
                          type="checkbox"
                          checked={candidate.selected}
                          disabled={
                            busyId === candidate.id ||
                            (!candidate.selected &&
                              count >= MAX_SELECTED_RESOURCES)
                          }
                          onChange={(e) =>
                            toggle(candidate.id, e.target.checked)
                          }
                        />
                        {candidate.thumbnail && (
                          <img
                            className="resource-thumb"
                            src={candidate.thumbnail}
                            alt=""
                            loading="lazy"
                            width={112}
                            height={63}
                          />
                        )}
                        <span className="resource-text">
                          <a
                            href={candidate.url}
                            target="_blank"
                            rel="noreferrer"
                            dir="auto"
                          >
                            {candidate.title}
                            <ExternalLink
                              size={12}
                              aria-label="(opens in a new tab)"
                            />
                          </a>
                          {candidate.video_count &&
                          candidate.video_count > 1 ? (
                            <span className="resource-count">
                              {candidate.video_count} videos
                            </span>
                          ) : null}
                          <small>
                            {candidate.publisher} —{" "}
                            {candidate.validation_reason}
                          </small>
                        </span>
                      </label>
                    </li>
                  );
                })}
            </ul>
          )}
        </div>
      ))}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

function SourceList({ job }: { job: ResearchJob }) {
  if (!job.sources.length) return null;
  return (
    <section className="review-section" aria-labelledby="sources-heading">
      <h3 id="sources-heading">Sources kept · {job.sources.length}</h3>
      <ul className="source-list">
        {job.sources.map((s) => (
          <li key={s.id}>
            <a href={s.url} target="_blank" rel="noreferrer" dir="auto">
              <Globe2 size={14} aria-hidden />
              <span>
                {s.title}
                <small>{s.publisher || host(s.url)} · opens in a new tab</small>
              </span>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}

const STEPS = ["Choose", "Research", "Review", "In library"];
function stepOf(job: ResearchJob | null) {
  if (!job) return 0;
  if (job.status === "review") return 2;
  if (job.status === "ready") return 3;
  return 1;
}

export function CreateFlow({
  open,
  onOpenChange,
  kind,
  onKindChange,
  documents,
  jobId,
  onPublished,
  onOpenDocument,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  kind: CreateKind;
  onKindChange: (kind: CreateKind) => void;
  documents: Document[];
  /** Resume this job instead of showing the form. */
  jobId?: string | null;
  onPublished: () => void;
  onOpenDocument: (id: string) => void;
}) {
  const [sourceDocument, setSourceDocument] = useState(""),
    [topic, setTopic] = useState(""),
    [urls, setUrls] = useState(""),
    [depth, setDepth] = useState("standard"),
    [level, setLevel] = useState("intermediate"),
    [goal, setGoal] = useState("Understand the ideas and apply them"),
    [job, setJob] = useState<ResearchJob | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    if (!open || !jobId) return;
    api
      .researchJob(jobId)
      .then(setJob)
      .catch((e) => setError(e.message));
  }, [open, jobId]);
  useEffect(() => {
    if (!open || !job || !ACTIVE_RESEARCH.has(job.status)) return;
    const timer = setInterval(
      () =>
        api
          .researchJob(job.id)
          .then(setJob)
          .catch((e) => setError(e.message)),
      1800,
    );
    return () => clearInterval(timer);
  }, [open, job?.id, job?.status]);
  // A fresh "create" entry starts at the form; a resumed job keeps its place.
  useEffect(() => {
    if (open && !jobId) {
      setJob(null);
      setError("");
    }
  }, [open, jobId]);
  const ready = documents.filter((d) => d.status === "ready");
  const canStart =
    kind === "companion"
      ? !!sourceDocument
      : kind === "topic"
        ? !!topic.trim()
        : !!urls.trim();
  async function start() {
    setBusy(true);
    setError("");
    try {
      setJob(
        await api.createResearch({
          topic: kind === "topic" ? topic : "",
          urls:
            kind === "url"
              ? urls
                  .split(/\n|,/)
                  .map((x) => x.trim())
                  .filter(Boolean)
              : [],
          depth,
          level,
          goal,
          material_type:
            kind === "companion" ? "learning_companion" : "study_guide",
          source_document_id: kind === "companion" ? sourceDocument : undefined,
        }),
      );
      onPublished();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function publish() {
    if (!job) return;
    setBusy(true);
    setError("");
    try {
      setJob(await api.publishResearch(job.id));
      onPublished();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const step = stepOf(job);
  const provenance = job
    ? job.source_document_id
      ? `Generated from “${documents.find((d) => d.id === job.source_document_id)?.title ?? "a library source"}”`
      : `Generated from ${job.sources.length} web ${job.sources.length === 1 ? "source" : "sources"}`
    : "";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="create-dialog">
        <DialogHeader>
          <DialogTitle>
            {job ? job.query || "Study guide" : "Create a study guide"}
          </DialogTitle>
          <DialogDescription>
            {job
              ? provenance
              : "StudyHub drafts it, you review it, and only then does it join your library."}
          </DialogDescription>
          <ol className="steps" aria-label="Progress">
            {STEPS.map((label, i) => (
              <li
                key={label}
                aria-current={i === step ? "step" : undefined}
                data-done={i < step || (i === 3 && step === 3) || undefined}
              >
                <span className="step-index" aria-hidden>
                  {i < step ? <Check size={12} /> : i + 1}
                </span>
                {label}
              </li>
            ))}
          </ol>
        </DialogHeader>
        <div className="create-body">
          {!job ? (
            <div className="create-form">
              <fieldset>
                <legend>Start from</legend>
                <div
                  className="choice-grid"
                  role="radiogroup"
                  aria-label="Start from"
                >
                  {KINDS.map(([value, label, description, Icon]) => (
                    <button
                      key={value}
                      type="button"
                      role="radio"
                      aria-checked={kind === value}
                      className="choice"
                      onClick={() => onKindChange(value)}
                    >
                      <Icon size={18} aria-hidden />
                      <strong>{label}</strong>
                      <span>{description}</span>
                    </button>
                  ))}
                </div>
              </fieldset>
              {kind === "companion" ? (
                <label className="field">
                  Source
                  <select
                    value={sourceDocument}
                    onChange={(e) => setSourceDocument(e.target.value)}
                  >
                    <option value="">Choose a ready source…</option>
                    {ready
                      .filter((d) => d.source_type === "uploaded")
                      .map((d) => (
                        <option value={d.id} key={d.id}>
                          {d.title}
                        </option>
                      ))}
                  </select>
                  <small>
                    Uses the source’s real chapters and indexed text. Each unit
                    links back to the original pages.
                  </small>
                </label>
              ) : kind === "topic" ? (
                <label className="field">
                  What do you want to learn?
                  <textarea
                    rows={3}
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                    placeholder="Apache Iceberg architecture, from fundamentals to production"
                    dir="auto"
                  />
                </label>
              ) : (
                <label className="field">
                  Public web addresses
                  <textarea
                    rows={4}
                    value={urls}
                    onChange={(e) => setUrls(e.target.value)}
                    placeholder={
                      "https://example.com/article\nhttps://example.org/official-guide"
                    }
                  />
                  <small>
                    One per line. Local and private-network addresses are
                    refused.
                  </small>
                </label>
              )}
              <label className="field">
                Your goal
                <input
                  value={goal}
                  onChange={(e) => setGoal(e.target.value)}
                  dir="auto"
                />
              </label>
              <details className="advanced">
                <summary>
                  Depth and level
                  <span className="hint">
                    {depth === "quick"
                      ? "Quick"
                      : depth === "deep"
                        ? "Deep"
                        : "Standard"}{" "}
                    · {level[0].toUpperCase() + level.slice(1)}
                  </span>
                </summary>
                <div className="field-row">
                  <label className="field">
                    Depth
                    <select
                      value={depth}
                      onChange={(e) => setDepth(e.target.value)}
                    >
                      <option value="quick">Quick overview</option>
                      <option value="standard">Standard</option>
                      <option value="deep">Deep research</option>
                    </select>
                  </label>
                  <label className="field">
                    Learner level
                    <select
                      value={level}
                      onChange={(e) => setLevel(e.target.value)}
                    >
                      <option value="beginner">Beginner</option>
                      <option value="intermediate">Intermediate</option>
                      <option value="advanced">Advanced</option>
                    </select>
                  </label>
                </div>
              </details>
              {error && (
                <p className="error" role="alert">
                  {error}
                </p>
              )}
              <div className="dialog-actions">
                <Button disabled={busy || !canStart} onClick={start}>
                  {busy
                    ? "Starting…"
                    : kind === "companion"
                      ? "Build guide"
                      : "Start research"}
                </Button>
              </div>
            </div>
          ) : ACTIVE_RESEARCH.has(job.status) ? (
            <div className="research-progress">
              <p role="status" className="research-message">
                {job.progress.message || "Working…"}
              </p>
              {job.progress.total ? (
                <progress
                  max={job.progress.total}
                  value={job.progress.completed || 0}
                  aria-label="Research progress"
                />
              ) : (
                <progress aria-label="Research progress" />
              )}
              <p className="hint">
                You can close this window. Research keeps running and stays
                listed in your library.
              </p>
              {job.outline.length > 0 && (
                <ol className="coverage">
                  {job.outline.map((x, i) => {
                    const done = i < (job.progress.completed || 0);
                    return (
                      <li key={i} data-done={done || undefined}>
                        {done ? (
                          <Check size={14} aria-label="Done" />
                        ) : (
                          <span className="dot" aria-label="Pending" />
                        )}
                        {x}
                      </li>
                    );
                  })}
                </ol>
              )}
              <SourceList job={job} />
              {job.resources?.length > 0 && (
                <p className="hint">
                  {job.resources.length} candidate videos and articles found so
                  far. You’ll choose among them at review.
                </p>
              )}
              <div className="dialog-actions">
                <Button
                  variant="ghost"
                  onClick={() => api.cancelResearch(job.id).then(setJob)}
                >
                  Stop research
                </Button>
              </div>
            </div>
          ) : job.status === "review" ? (
            <div className="review-layout">
              <article className="draft reading-prose" dir="auto">
                <Markdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    a: ({ href, children }) => (
                      <a href={href} target="_blank" rel="noreferrer">
                        {children}
                        <ExternalLink
                          size={11}
                          aria-label="(opens in a new tab)"
                        />
                      </a>
                    ),
                    h2: ({ children }) => <UnitHeading>{children}</UnitHeading>,
                    h3: ({ children }) => (
                      <StudyHeading>{children}</StudyHeading>
                    ),
                  }}
                >
                  {job.draft}
                </Markdown>
              </article>
              <aside className="review-aside">
                <SourceList job={job} />
                {job.material_type === "learning_companion" && (
                  <ResourceCurator job={job} onUpdated={setJob} />
                )}
              </aside>
              <div className="review-actions">
                <p className="hint">
                  Not in your library yet. Nothing is indexed until you add it.
                </p>
                {error && (
                  <p className="error" role="alert">
                    {error}
                  </p>
                )}
                <Confirm
                  trigger={<Button variant="ghost">Discard draft</Button>}
                  title="Discard this draft?"
                  description={`“${job.query}” will be removed from review and won’t be added to your library. This can’t be undone.`}
                  confirmLabel="Discard draft"
                  onConfirm={async () => {
                    await api.cancelResearch(job.id);
                    setJob(null);
                    onPublished();
                  }}
                />
                <Button disabled={busy} onClick={publish}>
                  <BookOpen size={15} aria-hidden />
                  {busy ? "Adding…" : "Add to library"}
                </Button>
              </div>
            </div>
          ) : job.status === "ready" ? (
            <div className="create-done" role="status">
              <FileText size={28} aria-hidden />
              <h3>Your study guide is in the library.</h3>
              <p className="hint">
                It’s labelled as a study guide. {provenance}.
              </p>
              <div className="dialog-actions">
                <Button variant="ghost" onClick={() => onOpenChange(false)}>
                  Back to library
                </Button>
                {job.document_id && (
                  <Button onClick={() => onOpenDocument(job.document_id!)}>
                    Open guide
                  </Button>
                )}
              </div>
            </div>
          ) : (
            <div className="create-done">
              <h3>
                {job.status === "cancelled"
                  ? "This research was stopped."
                  : "This research didn’t finish."}
              </h3>
              {job.error && (
                <p className="error" role="alert">
                  {job.error}
                </p>
              )}
              <div className="dialog-actions">
                <Button variant="ghost" onClick={() => setJob(null)}>
                  New request
                </Button>
                <Button onClick={() => api.retryResearch(job.id).then(setJob)}>
                  Try again
                </Button>
              </div>
            </div>
          )}
          {job && error && job.status !== "review" && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
