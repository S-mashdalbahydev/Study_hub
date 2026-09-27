"use client";
import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Popover } from "radix-ui";
import {
  ArrowUp,
  BookOpen,
  Copy,
  RefreshCw,
  RotateCcw,
  SlidersHorizontal,
  Square,
  X,
} from "lucide-react";
import { Button } from "./ui/button";
import {
  api,
  type Catalog,
  type Session,
  type Message,
  type Citation,
} from "@/lib/api";

const MODES: [Session["mode"], string, string][] = [
  ["guided", "Guided", "Step by step, checking understanding"],
  ["socratic", "Socratic", "Leads with questions"],
  ["deep-dive", "Deep dive", "More detail and nuance"],
  ["practical", "Practical", "Examples and application"],
];
const ACTIONS = [
  ["Explain", "explain"],
  ["Simplify", "simplify"],
  ["Give an example", "example"],
  ["Summarize", "summarize"],
] as const;

function modelName(session: Session, models: Catalog | null) {
  if (session.model === "studyhub-auto")
    return `Auto${models?.selected ? ` · ${models.selected.name}` : ""}`;
  return models?.models.find((m) => m.id === session.model)?.name ?? session.model;
}

function TutorOptions({
  session,
  models,
  onSettings,
  onRefreshModels,
}: {
  session: Session;
  models: Catalog | null;
  onSettings: (values: Partial<Session>) => void;
  onRefreshModels: () => void;
}) {
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <Button variant="ghost" size="icon" aria-label="Tutor options" title="Tutor options">
          <SlidersHorizontal size={16} aria-hidden />
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="menu popover" align="end" sideOffset={8}>
          <label className="field" htmlFor="study-mode">
            Study mode
            <select
              id="study-mode"
              value={session.mode}
              onChange={(e) =>
                onSettings({ mode: e.target.value as Session["mode"] })
              }
            >
              {MODES.map(([value, label]) => (
                <option value={value} key={value}>
                  {label}
                </option>
              ))}
            </select>
            <small>{MODES.find(([v]) => v === session.mode)?.[2]}</small>
          </label>
          <label className="field" htmlFor="model">
            Tutor model
            <span className="field-inline">
              <select
                id="model"
                value={session.model}
                onChange={(e) => onSettings({ model: e.target.value })}
              >
                <option value="studyhub-auto">
                  Auto{models?.selected ? ` · ${models.selected.model}` : ""}
                </option>
                {models?.models.map((m) => (
                  <option value={m.id} key={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Refresh models"
                title="Refresh models"
                onClick={onRefreshModels}
              >
                <RefreshCw size={14} aria-hidden />
              </Button>
            </span>
            <small>
              Local models run on this machine; API connections send the
              question and cited passages to that provider. Manage both in
              Settings.
            </small>
          </label>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

export function ChatPanel({
  session,
  models,
  selection,
  onClearSelection,
  onCitation,
  onSettings,
  onRefreshModels,
  beforeSend,
  ready = true,
  preparing = "Preparing",
  onClose,
}: {
  session: Session;
  models: Catalog | null;
  selection: string;
  onClearSelection: () => void;
  onCitation: (c: Citation) => void;
  onSettings: (values: Partial<Session>) => void;
  onRefreshModels: () => void;
  beforeSend: () => Promise<void>;
  ready?: boolean;
  preparing?: string;
  onClose?: () => void;
}) {
  const [messages, setMessages] = useState<Message[]>(session.messages || []),
    [text, setText] = useState(""),
    [busy, setBusy] = useState(false),
    [activity, setActivity] = useState(""),
    [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null),
    bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setMessages(session.messages || []);
    return () => controller.current?.abort();
  }, [session.id]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages, activity]);
  const scope = selection ? "the selected passage" : `page ${session.page}`;
  async function send(question = text, intent = "ask") {
    if (busy || !question.trim()) return;
    setBusy(true);
    setError("");
    setText("");
    setActivity("Reading the relevant passages…");
    const abort = new AbortController();
    controller.current = abort;
    const temp = crypto.randomUUID();
    let answerId = temp;
    const current: Message = {
      id: temp,
      role: "assistant",
      content: "",
      citations: [],
      status: "generating",
    };
    setMessages((old) => [
      ...old,
      {
        id: crypto.randomUUID(),
        role: "user",
        content: question,
        citations: [],
        status: "complete",
      },
      current,
    ]);
    function update() {
      setMessages((old) =>
        old.map((m) =>
          m.id === temp || m.id === answerId ? { ...current } : m,
        ),
      );
    }
    try {
      await beforeSend();
      await api.chat(
        session.id,
        question,
        selection,
        intent,
        abort.signal,
        (part) => {
          if (part.type === "start") {
            answerId = part.message_id;
            current.id = answerId;
            update();
          }
          if (part.type === "status") setActivity(part.text);
          if (part.type === "text") {
            setActivity("");
            current.content += part.text;
            update();
          }
          if (part.type === "citations") {
            current.citations = part.citations;
            update();
          }
          if (part.type === "done") {
            current.status = part.status;
            update();
          }
          if (part.type === "error") {
            current.status = "failed";
            setError(part.text);
            update();
          }
        },
      );
      onClearSelection();
    } catch (e) {
      current.status = abort.signal.aborted ? "cancelled" : "failed";
      if (!abort.signal.aborted) setError((e as Error).message);
      update();
    } finally {
      setBusy(false);
      setActivity("");
      controller.current = null;
    }
  }
  const mode = MODES.find(([v]) => v === session.mode)?.[1] ?? "Guided";
  return (
    <section className="chat-panel" aria-label="Tutor">
      <header className="chat-heading">
        <div>
          <h2>Tutor</h2>
          <p className="hint">
            {mode} · {modelName(session, models)}
          </p>
        </div>
        <div className="chat-heading-actions">
          <TutorOptions
            session={session}
            models={models}
            onSettings={onSettings}
            onRefreshModels={onRefreshModels}
          />
          {onClose && (
            <Button
              variant="ghost"
              size="icon"
              aria-label="Close tutor"
              title="Close tutor"
              onClick={onClose}
            >
              <X size={16} aria-hidden />
            </Button>
          )}
        </div>
      </header>
      <div className="messages" aria-live="polite" aria-relevant="additions text">
        {!messages.length && (
          <div className="empty-chat">
            <h3>{ready ? "Ask about this page." : "Keep reading for now."}</h3>
            <p>
              {ready
                ? "Answers come from this source and cite the pages they use. Select a passage to ask about it specifically."
                : `Source-grounded questions unlock when indexing finishes. Current step: ${preparing.toLowerCase()}.`}
            </p>
          </div>
        )}
        {messages.map((m, i) => (
          <article
            className={`message ${m.role}`}
            key={m.id}
            data-testid={`${m.role}-message`}
          >
            {m.role === "assistant" && (
              <div className="message-label">Tutor</div>
            )}
            <div className="markdown" dir="auto">
              <Markdown
                remarkPlugins={[remarkGfm]}
                components={{
                  a: ({ href, children }) => (
                    <a href={href} target="_blank" rel="noreferrer">
                      {children}
                    </a>
                  ),
                }}
              >
                {m.content}
              </Markdown>
            </div>
            {m.citations.length > 0 && (
              <div className="citations" aria-label="Sources from your document">
                <span className="citations-label">Evidence</span>
                {m.citations.map((c) => (
                  <button
                    className="citation"
                    key={c.id}
                    onClick={() => onCitation(c)}
                    data-testid="citation"
                    title={c.page ? `Go to page ${c.page}` : `Go to ${c.label}`}
                  >
                    <BookOpen size={12} aria-hidden />
                    {c.id} · {c.page ? `Page ${c.page}` : c.label}
                  </button>
                ))}
              </div>
            )}
            {m.role === "assistant" &&
              m.status === "complete" &&
              !m.citations.length &&
              m.content && (
                <p className="hint ungrounded">
                  No source passages were attached to this answer.
                </p>
              )}
            {m.role === "assistant" && m.status !== "generating" && (
              <div className="message-tools">
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Copy response"
                  title="Copy"
                  onClick={() =>
                    navigator.clipboard
                      .writeText(m.content)
                      .catch(() =>
                        setError("Copy is unavailable in this browser."),
                      )
                  }
                >
                  <Copy size={14} aria-hidden />
                </Button>
                {i === messages.length - 1 && (
                  <Button
                    variant="ghost"
                    size="icon"
                    disabled={busy}
                    aria-label="Regenerate response"
                    title="Ask again"
                    onClick={() =>
                      send(
                        [...messages.slice(0, i)]
                          .reverse()
                          .find((x) => x.role === "user")?.content ||
                          "Explain this page.",
                      )
                    }
                  >
                    <RotateCcw size={14} aria-hidden />
                  </Button>
                )}
                {m.status !== "complete" && (
                  <small className="hint">
                    {m.status === "cancelled" ? "Stopped" : "Reply interrupted"}
                  </small>
                )}
              </div>
            )}
          </article>
        ))}
        {activity && (
          <p role="status" className="hint activity">
            {activity}
          </p>
        )}
        <div ref={bottom} />
      </div>
      <div className="composer">
        <div className="scope" aria-live="polite">
          {selection ? (
            <div className="selection-bar">
              <span className="scope-label">Selected passage</span>
              <q dir="auto">
                {selection.slice(0, 110)}
                {selection.length > 110 ? "…" : ""}
              </q>
              <button aria-label="Clear selection" onClick={onClearSelection}>
                <X size={14} aria-hidden />
              </button>
            </div>
          ) : (
            <p className="hint">
              Asking about <strong>page {session.page}</strong> of this source.
              Select text to narrow it.
            </p>
          )}
        </div>
        <div className="suggestions" aria-label={`Quick actions for ${scope}`}>
          {ACTIONS.map(([label, intent]) => (
            <button
              key={intent}
              disabled={busy || !ready}
              onClick={() =>
                send(
                  `${label} ${selection ? "the selected passage" : "the current page"}.`,
                  intent,
                )
              }
            >
              {label}
            </button>
          ))}
        </div>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
          className="composer-box"
        >
          <textarea
            aria-label="Ask about this document"
            disabled={!ready}
            dir="auto"
            rows={2}
            placeholder={
              ready ? `Ask about ${scope}…` : "Available once indexing finishes"
            }
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                send();
              }
            }}
          />
          <div className="composer-actions">
            <span className="hint">Ctrl / ⌘ + Enter to send</span>
            {busy ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                aria-label="Stop generation"
                onClick={() => controller.current?.abort()}
              >
                <Square size={12} aria-hidden /> Stop
              </Button>
            ) : (
              <Button
                type="submit"
                size="icon"
                aria-label="Send message"
                disabled={!ready || !text.trim()}
              >
                <ArrowUp size={18} aria-hidden />
              </Button>
            )}
          </div>
        </form>
      </div>
    </section>
  );
}
