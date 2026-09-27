"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Layers3 } from "lucide-react";
import { api, type LearningSession } from "@/lib/api";
import { shortDate } from "@/lib/labels";
import { AppHeader } from "./app-header";

export function StudySpaces() {
  const [spaces, setSpaces] = useState<LearningSession[] | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    api
      .learningSessions()
      // The list omits sources and chapters; each space is small, so load them.
      .then((list) =>
        Promise.all(list.map((s) => api.learningSession(s.id).catch(() => null))),
      )
      .then((full) => setSpaces(full.filter((s): s is LearningSession => !!s)))
      .catch((e) => setError(e.message));
  }, []);
  return (
    <div className="app">
      <AppHeader />
      <main id="main" className="shell">
        <section className="page-intro">
          <h1>Study spaces</h1>
          <p>
            Each space groups sources around one goal. Chapters are ticked off
            as you read them; lessons and exercises cite their pages.
          </p>
        </section>
        {error && (
          <p className="notice error" role="alert">
            {error}
          </p>
        )}
        {!spaces && !error && (
          <p className="notice" role="status">
            Loading study spaces…
          </p>
        )}
        {spaces && !spaces.length && (
          <section className="empty-state">
            <Layers3 size={32} strokeWidth={1.4} aria-hidden />
            <h2>No study spaces yet.</h2>
            <p>
              Create one from the Library with <strong>Add or create → New
              study space</strong> once you have two ready sources.
            </p>
            <Link href="/" className="text-link">
              Go to the library
            </Link>
          </section>
        )}
        {spaces && spaces.length > 0 && (
          <ul className="spaces-index">
            {spaces.map((space) => {
              const chapters = space.themes.flatMap((t) => t.chapters);
              const read = chapters.filter((c) => c.done).length;
              return (
                <li key={space.id}>
                  <Link href={`/sessions/${space.id}`} className="space-row">
                    <span className="space-row-main">
                      <strong dir="auto">{space.title}</strong>
                      <span dir="auto">{space.learning_goal}</span>
                      <small>
                        {space.materials.map((m) => m.title).join(" · ")}
                      </small>
                    </span>
                    <span className="space-row-meta">
                      {chapters.length ? (
                        <>
                          <span>
                            {read} of {chapters.length} chapters read
                          </span>
                          <span className="meter" aria-hidden>
                            <span
                              style={{
                                width: `${(read / chapters.length) * 100}%`,
                              }}
                            />
                          </span>
                        </>
                      ) : (
                        <span>Topic map not built yet</span>
                      )}
                      <small>
                        {space.materials.length} sources · last active{" "}
                        {shortDate(space.updated_at)}
                      </small>
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </main>
    </div>
  );
}
