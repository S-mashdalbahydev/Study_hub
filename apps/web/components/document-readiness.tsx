import { Check, Circle, LoaderCircle, X } from "lucide-react";
import type { Document } from "@/lib/api";

const ORDER = ["discovered", "parsing", "parsed", "chunking", "embedding", "ready"];
const reached = (status: string, phase: string) =>
  ORDER.indexOf(status) >= ORDER.indexOf(phase);

export function readiness(doc: Document) {
  return {
    readable:
      doc.kind === "pdf"
        ? doc.status !== "failed"
        : reached(doc.status, "parsed"),
    outline: reached(doc.status, "parsed") && doc.status !== "failed",
    embeddings: doc.status === "ready",
    failed: doc.status === "failed",
  };
}

/** Preparation steps written out in words; icons only reinforce the text. */
export function DocumentReadiness({ doc }: { doc: Document }) {
  const state = readiness(doc);
  const steps = [
    ["Reading", state.readable, "Open and read the original"],
    ["Outline", state.outline, "Chapters and figures extracted"],
    ["Tutor", state.embeddings, "Questions grounded in this source"],
  ] as const;
  return (
    <ol className="readiness" aria-label="Preparation steps">
      {steps.map(([label, ready, description]) => {
        const status = state.failed
          ? "Failed"
          : ready
            ? "Ready"
            : doc.status === "discovered"
              ? "Waiting"
              : "In progress";
        return (
          <li key={label} data-state={status.toLowerCase().replace(" ", "-")}>
            {state.failed ? (
              <X size={14} aria-hidden />
            ) : ready ? (
              <Check size={14} aria-hidden />
            ) : doc.status === "discovered" ? (
              <Circle size={14} aria-hidden />
            ) : (
              <LoaderCircle size={14} className="spin" aria-hidden />
            )}
            <span>
              <strong>{label}</strong> — {status}
              <small>{description}</small>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
