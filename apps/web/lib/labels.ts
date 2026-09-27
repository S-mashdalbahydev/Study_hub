import type { Document } from "./api";

// Display vocabulary. Stored names (source_type, learning-sessions, …) stay as
// they are; only what the reader sees is mapped here.
export const isGuide = (doc: Pick<Document, "source_type">) =>
  doc.source_type !== "uploaded";

export function kindLabel(doc: Document) {
  if (doc.source_type === "learning_companion") return "Study guide · from a source";
  if (doc.source_type === "research_book") return "Study guide · researched";
  return `Source · ${doc.kind.toUpperCase()}`;
}

/** One human sentence for the processing state. */
export function statusText(doc: Document) {
  switch (doc.status) {
    case "ready":
      return "Ready";
    case "failed":
      return "Couldn’t prepare this item";
    case "discovered":
      return "Waiting to prepare";
    case "parsing":
      return "Reading the file";
    case "parsed":
    case "chunking":
      return "Preparing outline";
    case "embedding":
      return "Indexing for the tutor";
    default:
      return "Preparing";
  }
}

export const shortDate = (value: string) =>
  new Date(value).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
