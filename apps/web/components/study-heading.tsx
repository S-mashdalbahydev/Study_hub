import { Children, isValidElement } from "react";

type Kind = "explanation" | "exercise" | "resource" | "section";
const LABEL: Record<Kind, string> = {
  explanation: "Explanation",
  exercise: "Exercise",
  resource: "External resources",
  section: "",
};
// Generated guides mark section types with an emoji; the reader sees a word instead.
const EMOJI: [string, Kind][] = [
  ["💡", "explanation"],
  ["🧠", "explanation"],
  ["🔎", "explanation"],
  ["✍️", "exercise"],
  ["✍", "exercise"],
  ["🌱", "exercise"],
  ["🌐", "resource"],
];
const LEADING_SYMBOLS = /^[\s\p{Extended_Pictographic}️‍]+/u;
/** A title as plain words, without the decorative emoji generated guides add. */
export const plainTitle = (value: string) => value.replace(LEADING_SYMBOLS, "");

function text(node: React.ReactNode): string {
  return Children.toArray(node)
    .map((child) =>
      typeof child === "string" || typeof child === "number"
        ? String(child)
        : isValidElement<{ children?: React.ReactNode }>(child)
          ? text(child.props.children)
          : "",
    )
    .join("");
}

export function headingKind(node: React.ReactNode): Kind {
  const value = text(node);
  const marked = EMOJI.find(([emoji]) => value.includes(emoji));
  if (marked) return marked[1];
  if (/\b(exercise|practice|quiz|try it|apply)\b/i.test(value))
    return "exercise";
  if (/\b(resources?|videos?|further reading)\b/i.test(value))
    return "resource";
  return "section";
}

export function strip(node: React.ReactNode): React.ReactNode {
  let first = true;
  return Children.map(node, (child) => {
    if (first && typeof child === "string") {
      first = false;
      return child.replace(LEADING_SYMBOLS, "");
    }
    first = false;
    return child;
  });
}

export function StudyHeading({
  children,
  id,
}: {
  children?: React.ReactNode;
  id?: string;
}) {
  const kind = headingKind(children);
  return (
    <h3 className="study-section" data-kind={kind} id={id}>
      {LABEL[kind] && <span className="section-kicker">{LABEL[kind]}</span>}
      <span>{strip(children)}</span>
    </h3>
  );
}

/** A guide's unit heading, with its decorative emoji removed. */
export function UnitHeading({
  children,
  id,
}: {
  children?: React.ReactNode;
  id?: string;
}) {
  return (
    <h2 className="study-unit" id={id}>
      {strip(children)}
    </h2>
  );
}
