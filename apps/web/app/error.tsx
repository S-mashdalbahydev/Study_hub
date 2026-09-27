"use client";
import Link from "next/link";
import { Button } from "@/components/ui/button";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <main id="main" className="shell">
      <section className="empty-state">
        <h1 className="dialog-title">Something interrupted this page.</h1>
        <p>
          Your sources, reading sessions and study spaces are still saved in
          StudyHub.
        </p>
        <div className="actions">
          <Button onClick={reset}>Try again</Button>
          <Button variant="outline" asChild>
            <Link href="/">Go to the library</Link>
          </Button>
        </div>
      </section>
    </main>
  );
}
