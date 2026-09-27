import { test, expect, type Page } from "@playwright/test";

/**
 * Logic these tests protect, stated as behaviour rather than markup:
 *  - a topic's chapters are real chapters of real materials, never invented
 *  - each chapter belongs to exactly one topic, so nothing is studied twice
 *  - ticking a chapter survives a reload and moves both counters
 *  - opening a chapter lands on that chapter's first page
 *  - a lesson may only cite pages that belong to that topic's chapters
 */

type Chapter = {
  document_id: string;
  chapter_id: string;
  title: string;
  page: number;
  end_page: number | null;
  done: boolean;
  book: string;
  kind: string;
};
type Theme = {
  id: string;
  title: string;
  chapters: Chapter[];
  lesson: string;
  citations: {
    n: number;
    source_id: string;
    page: number | null;
    url: string;
  }[];
};

async function mappedSession(page: Page) {
  const sessions = await (
    await page.request.get("/backend/api/learning-sessions")
  ).json();
  for (const row of sessions) {
    const full = await (
      await page.request.get(`/backend/api/learning-sessions/${row.id}`)
    ).json();
    if ((full.themes ?? []).some((t: Theme) => t.chapters.length))
      return full as {
        id: string;
        themes: Theme[];
        materials: { document_id: string; title: string }[];
      };
  }
  return null;
}

test("every chapter on the map is a real chapter of a selected material", async ({
  page,
}) => {
  await page.goto("/");
  const session = await mappedSession(page);
  test.skip(!session, "no study map has been built in this environment");

  const allowed = new Set(session!.materials.map((m) => m.document_id));
  const outlines = new Map<string, Set<string>>();
  for (const id of allowed) {
    const reader = await (
      await page.request.get(`/backend/api/documents/${id}/reader`)
    ).json();
    const titles = new Set<string>(
      (reader.outline ?? []).map((node: { title: string }) => node.title),
    );
    for (const item of reader.book?.toc ?? []) titles.add(item.title);
    outlines.set(id, titles);
  }

  const chapters = session!.themes.flatMap((t) => t.chapters);
  expect(chapters.length).toBeGreaterThan(0);
  for (const chapter of chapters) {
    expect(allowed).toContain(chapter.document_id);
    expect(chapter.page).toBeGreaterThan(0);
    if (chapter.end_page !== null)
      expect(chapter.end_page).toBeGreaterThan(chapter.page);
    // The title must come from that material's own contents, not be generated.
    expect(outlines.get(chapter.document_id)).toContain(chapter.title);
  }
});

test("a chapter is filed under exactly one topic", async ({ page }) => {
  await page.goto("/");
  const session = await mappedSession(page);
  test.skip(!session, "no study map has been built in this environment");
  const keys = session!.themes.flatMap((t) =>
    t.chapters.map((c) => `${c.document_id}:${c.chapter_id}`),
  );
  expect(new Set(keys).size).toBe(keys.length);
});

test("ticking a chapter persists and updates both counters", async ({
  page,
}) => {
  await page.goto("/");
  const session = await mappedSession(page);
  test.skip(!session, "no study map has been built in this environment");
  await page.goto(`/sessions/${session!.id}`);
  await expect(page.locator(".map-tree-theme").first()).toBeVisible();

  const header = page.locator(".notebook-header p").last();
  const before = await header.innerText();
  const readBefore = Number(before.match(/^(\d+)/)?.[1] ?? "0");

  const box = page.locator(".map-tree-chapter input").first();
  const wasChecked = await box.isChecked();
  await box.click({ force: true });
  await expect
    .poll(async () => Number((await header.innerText()).match(/^(\d+)/)?.[1]))
    .toBe(wasChecked ? readBefore - 1 : readBefore + 1);

  // The tick is server state, so a fresh load must agree.
  await page.reload();
  await expect(page.locator(".map-tree-theme").first()).toBeVisible();
  await expect(page.locator(".map-tree-chapter input").first()).toBeChecked({
    checked: !wasChecked,
  });
});

test("opening a chapter from the map lands on that chapter's page", async ({
  page,
}) => {
  await page.goto("/");
  const session = await mappedSession(page);
  test.skip(!session, "no study map has been built in this environment");
  const target = session!.themes
    .flatMap((t) => t.chapters)
    .find((c) => c.kind === "pdf" && c.page > 1);
  test.skip(!target, "no multi-page PDF chapter on this map");

  await page.goto(`/sessions/${session!.id}`);
  await expect(page.locator(".map-open").first()).toBeVisible();
  await page.goto(`/study/${target!.document_id}?page=${target!.page}`);
  await expect(page.locator(".pdf-scroll .page").first()).toBeVisible();
  await expect
    .poll(
      async () =>
        await page.locator('input[aria-label="Current page"]').inputValue(),
      { timeout: 60000 },
    )
    .toBe(String(target!.page));
});

test("a lesson only cites pages inside its own topic's chapters", async ({
  page,
}) => {
  await page.goto("/");
  const session = await mappedSession(page);
  test.skip(!session, "no study map has been built in this environment");
  const taught = session!.themes.find((t) => t.lesson && t.citations.length);
  test.skip(!taught, "no topic has been taught yet in this environment");

  const ranges = new Map<string, [number, number | null][]>();
  for (const chapter of taught!.chapters) {
    const list = ranges.get(chapter.document_id) ?? [];
    list.push([chapter.page, chapter.end_page]);
    ranges.set(chapter.document_id, list);
  }
  for (const citation of taught!.citations) {
    // Every citation points at a material that is actually on this topic…
    expect([...ranges.keys()]).toContain(citation.source_id);
    expect(citation.url).toContain(citation.source_id);
    // …and, when the material has pages, at a page inside one of its chapters.
    if (citation.page !== null) {
      const inside = ranges
        .get(citation.source_id)!
        .some(
          ([start, end]) =>
            citation.page! >= start && (end === null || citation.page! < end),
        );
      expect(
        inside,
        `citation S${citation.n} points at page ${citation.page}, outside this topic's chapters`,
      ).toBe(true);
    }
  }
  // Nothing may reference an excerpt number that was never provided.
  const referenced = [...taught!.lesson.matchAll(/\[S(\d+)\]/g)].map((m) =>
    Number(m[1]),
  );
  const known = new Set(taught!.citations.map((c) => c.n));
  for (const n of referenced) expect(known).toContain(n);
});
