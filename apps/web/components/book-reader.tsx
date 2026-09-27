"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Search,
} from "lucide-react";
import { Button } from "./ui/button";
import { api, type Book, type Document } from "@/lib/api";
import { StudyHeading, plainTitle, strip } from "./study-heading";

export default function BookReader({
  book,
  doc,
  page,
  onPage,
  onSelection,
}: {
  book: Book;
  doc: Document;
  page: number;
  onPage: (page: number) => void;
  onSelection: (text: string) => void;
}) {
  const holder = useRef<HTMLDivElement>(null);
  const current = Math.max(1, Math.min(page, book.page_count));
  const readerPage = book.pages.find((item) => item.number === current);
  const [query, setQuery] = useState("");
  const [notice, setNotice] = useState("");
  const chapter = useMemo(
    () =>
      [...book.toc]
        .reverse()
        .find((item) => item.kind === "chapter" && item.reader_page <= current),
    [book, current],
  );
  useEffect(() => {
    holder.current?.scrollTo({ top: 0, behavior: "auto" });
  }, [current]);
  function go(next: number) {
    onPage(Math.max(1, Math.min(book.page_count, next)));
  }
  function find(event: React.FormEvent) {
    event.preventDefault();
    const value = query.trim().toLowerCase();
    if (!value) return;
    const found = book.pages.find((item) =>
      item.blocks.some((block) => block.markdown.toLowerCase().includes(value)),
    );
    if (found) {
      go(found.number);
      setNotice(`Found on reader page ${found.number}.`);
    } else setNotice("No matching text found.");
  }
  return (
    <section className="reader book-reader" aria-label="Digital book reader">
      <div className="book-toolbar">
        <div>
          <span className="kind">
            {chapter
              ? plainTitle(chapter.title)
              : current === 1
                ? "Opening"
                : "Book"}
          </span>
          <span className="book-page-label">
            Page {current} of {book.page_count}
          </span>
        </div>
        <form onSubmit={find} role="search" className="tool-group find">
          <Search size={15} aria-hidden />
          <input
            type="search"
            aria-label="Find in book"
            placeholder="Find in book"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Button size="sm" variant="ghost">
            Find
          </Button>
        </form>
        <span className="hint find-status" role="status">
          {notice}
        </span>
      </div>
      <div
        className="book-page-stage"
        ref={holder}
        onMouseUp={() => {
          const selection = window.getSelection();
          if (
            selection?.toString().trim() &&
            holder.current?.contains(selection.anchorNode)
          )
            onSelection(selection.toString().slice(0, 3000));
        }}
      >
        {current === 1 ? (
          <article className="book-cover-page">
            <div className="book-cover-art">
              {book.cover ? (
                <img src={api.cover(doc.id)} alt="Book cover" />
              ) : (
                <div className="book-cover-placeholder">
                  <span className="kind">StudyHub edition</span>
                  <strong dir="auto">{book.title}</strong>
                </div>
              )}
            </div>
            <div className="book-cover-copy">
              <p className="kind">Reading edition</p>
              <h2 className="book-title" dir="auto">
                {book.title}
              </h2>
              {book.subtitle && (
                <p className="book-subtitle">{book.subtitle}</p>
              )}
              <dl>
                <div>
                  <dt>
                    <Clock3 size={14} aria-hidden /> Reading time
                  </dt>
                  <dd>{book.estimated_minutes} min</dd>
                </div>
                <div>
                  <dt>
                    <BookOpen size={14} aria-hidden /> Structure
                  </dt>
                  <dd>
                    {book.chapter_count || 1} chapters · {book.pages.length}{" "}
                    pages
                  </dd>
                </div>
              </dl>
              <p className="source-note">Imported from {book.filename}</p>
              <Button onClick={() => go(2)} disabled={book.page_count < 2}>
                Start reading <ChevronRight size={16} />
              </Button>
            </div>
          </article>
        ) : (
          <article
            key={readerPage?.id}
            className="reader-text book-sheet markdown reading-prose"
            dir="auto"
            aria-label={`Reader page ${current}`}
          >
            {readerPage?.blocks.map((block) => (
              <section
                key={block.id}
                id={block.id}
                className={`book-block block-${block.type}`}
                data-origin={block.origin}
              >
                {block.origin !== "original" && (
                  <span className="origin-label">
                    {block.origin === "ai"
                      ? "Explanation · generated"
                      : "External resource"}
                  </span>
                )}
                <Markdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    a: ({ href, children }) => (
                      <a href={href} target="_blank" rel="noreferrer">
                        {children}
                      </a>
                    ),
                    h1: ({ children }) => <h1>{children}</h1>,
                    h2: ({ children }) => <h2>{strip(children)}</h2>,
                    h3: ({ children }) => (
                      <StudyHeading>{children}</StudyHeading>
                    ),
                    h4: ({ children }) => <h4>{children}</h4>,
                    img: ({ src, alt }) => (
                      <figure>
                        <img src={src} alt={alt || "Figure"} />
                        <figcaption>{alt || "Source figure"}</figcaption>
                      </figure>
                    ),
                  }}
                >
                  {block.markdown}
                </Markdown>
              </section>
            ))}
            <p className="page-source">Original source · {book.filename}</p>
          </article>
        )}
      </div>
      <nav className="book-pagination" aria-label="Reader page navigation">
        <Button
          variant="ghost"
          disabled={current <= 1}
          onClick={() => go(current - 1)}
        >
          <ChevronLeft size={16} /> Previous
        </Button>
        <span>
          {current} / {book.page_count}
        </span>
        <Button
          variant="ghost"
          disabled={current >= book.page_count}
          onClick={() => go(current + 1)}
        >
          Next <ChevronRight size={16} />
        </Button>
      </nav>
    </section>
  );
}
