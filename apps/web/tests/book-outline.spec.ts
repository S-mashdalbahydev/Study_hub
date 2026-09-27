import { test, expect } from "@playwright/test";

test("book outline is a progressive semantic tree with independent navigation", async ({
  page,
}) => {
  let sessionPage = 1;
  const toc = [
    {
      id: "introduction",
      title: "Introduction",
      kind: "chapter",
      level: 2,
      parent_id: null,
      reader_page: 2,
    },
    {
      id: "chapter-1-foundations",
      title: "Chapter 1 — Foundations",
      kind: "chapter",
      level: 2,
      parent_id: null,
      reader_page: 3,
    },
    {
      id: "1-1-introduction",
      title: "1.1 Introduction",
      kind: "section",
      level: 3,
      parent_id: "chapter-1-foundations",
      reader_page: 4,
      source_start_page: 4,
      source_end_page: 6,
    },
    {
      id: "1-2-data-types",
      title: "1.2 Data Types",
      kind: "section",
      level: 3,
      parent_id: "chapter-1-foundations",
      reader_page: 5,
      source_start_page: 7,
      source_end_page: 12,
    },
    {
      id: "structured-data",
      title: "Structured Data",
      kind: "subsection",
      level: 4,
      parent_id: "1-2-data-types",
      reader_page: 5,
      source_start_page: 7,
      source_end_page: 9,
    },
    {
      id: "unstructured-data",
      title: "Unstructured Data",
      kind: "subsection",
      level: 4,
      parent_id: "1-2-data-types",
      reader_page: 6,
      source_start_page: 10,
      source_end_page: 12,
    },
    {
      id: "1-3-storage",
      title: "1.3 Storage",
      kind: "section",
      level: 3,
      parent_id: "chapter-1-foundations",
      reader_page: 7,
      source_start_page: 13,
      source_end_page: 17,
    },
    {
      id: "chapter-2-architecture",
      title: "Chapter 2 — Architecture",
      kind: "chapter",
      level: 2,
      parent_id: null,
      reader_page: 8,
    },
    {
      id: "2-2-components",
      title: "2.2 Components",
      kind: "section",
      level: 3,
      parent_id: "chapter-2-architecture",
      reader_page: 9,
    },
    {
      id: "authentication",
      title: "Authentication",
      kind: "subsection",
      level: 4,
      parent_id: "2-2-components",
      reader_page: 9,
    },
    {
      id: "references",
      title: "References",
      kind: "chapter",
      level: 2,
      parent_id: null,
      reader_page: 10,
    },
  ];
  const pages = Array.from({ length: 9 }, (_, index) => {
    const number = index + 2;
    const item = [...toc].reverse().find((node) => node.reader_page === number);
    return {
      id: `reader-page-${number}`,
      number,
      blocks: [
        {
          id: item?.id || `content-${number}`,
          type: "heading",
          markdown: `## ${item?.title || `Content ${number}`}`,
          origin: "original",
          level: 2,
        },
      ],
    };
  });
  await page.route("**/backend/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/api/documents/book/reader"))
      return route.fulfill({
        json: {
          page_count: null,
          cover: false,
          outline: [],
          content: "",
          chunks: [],
          book: {
            id: "book",
            title: "Data Book",
            subtitle: "A semantic fixture",
            authors: [],
            filename: "data-book.md",
            cover: false,
            estimated_minutes: 20,
            chapter_count: 2,
            page_count: 10,
            toc,
            pages,
          },
        },
      });
    if (path.endsWith("/api/documents/book"))
      return route.fulfill({
        json: {
          id: "book",
          title: "Data Book",
          filename: "data-book.md",
          kind: "md",
          status: "ready",
          error: null,
          created_at: "2026-01-01",
          source_type: "research_book",
        },
      });
    if (
      path.endsWith("/api/sessions/session") &&
      request.method() === "PATCH"
    ) {
      sessionPage = request.postDataJSON().page ?? sessionPage;
      return route.fulfill({ json: session() });
    }
    if (path.endsWith("/api/sessions/session"))
      return route.fulfill({ json: { ...session(), messages: [] } });
    if (path.endsWith("/api/config"))
      return route.fulfill({
        json: {
          tutor: {
            models: [],
            available: false,
            selected: null,
            error: null,
            warnings: [],
          },
        },
      });
    return route.fulfill({ status: 404, json: { detail: "not mocked" } });
  });
  function session() {
    return {
      id: "session",
      document_id: "book",
      page: sessionPage,
      model: "studyhub-auto",
      mode: "guided",
      layout: "reading",
      zoom: 1,
      sidebar: true,
      chat_width: 42,
      updated_at: "2026-01-01",
    };
  }

  await page.goto("/study/book?session=session");
  const tree = page.getByRole("tree", { name: "Book outline" });
  const chapter1 = tree.getByRole("treeitem", {
    name: /Chapter 1 — Foundations/,
  });
  const chapter2 = tree.getByRole("treeitem", {
    name: /Chapter 2 — Architecture/,
  });
  await expect(chapter1).toHaveAttribute("aria-expanded", "false");
  await expect(tree.getByText("1.1 Introduction")).toHaveCount(0);
  await chapter2.focus();
  await chapter2.press("ArrowRight");
  await expect(chapter2).toHaveAttribute("aria-expanded", "true");
  await expect(
    page
      .locator(".book-cover-copy")
      .getByRole("heading", { name: "Data Book" }),
  ).toBeVisible();
  await chapter2.press("ArrowLeft");
  await expect(chapter2).toHaveAttribute("aria-expanded", "false");
  await page.screenshot({ path: "test-results/book-outline-root.png" });

  await chapter1.getByRole("button", { name: /Expand Chapter 1/ }).click();
  await expect(
    page
      .locator(".book-cover-copy")
      .getByRole("heading", { name: "Data Book" }),
  ).toBeVisible();
  await expect(tree.getByText("1.1 Introduction")).toBeVisible();
  await expect(chapter2).toHaveAttribute("aria-expanded", "false");
  await chapter1.locator(":scope > .outline-tree-row > .tree-label").click();
  await expect(
    page.getByRole("heading", { name: "Chapter 1 — Foundations" }),
  ).toBeVisible();

  const dataTypes = tree.getByRole("treeitem", { name: /1.2 Data Types/ });
  await expect(chapter1).toHaveAttribute("aria-level", "1");
  await expect(dataTypes).toHaveAttribute("aria-level", "2");
  await dataTypes
    .getByRole("button", { name: /Expand 1.2 Data Types/ })
    .click();
  await tree
    .getByRole("treeitem", { name: /Structured Data/ })
    .locator(":scope > .outline-tree-row > .tree-label")
    .click();
  await expect(
    tree.getByRole("treeitem", { name: /Structured Data/ }),
  ).toHaveAttribute("aria-level", "3");
  await expect(
    page.getByRole("heading", { name: "Structured Data" }),
  ).toBeVisible();
  await expect(
    tree.getByRole("treeitem", { name: /Structured Data/ }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(chapter1).toHaveAttribute("aria-selected", "false");
  await expect(dataTypes).toHaveAttribute("aria-selected", "false");
  await expect(chapter1).toHaveAttribute("aria-expanded", "true");
  await expect(dataTypes).toHaveAttribute("aria-expanded", "true");
  await page.screenshot({
    path: "test-results/book-outline-nested-active.png",
  });

  await dataTypes
    .getByRole("button", { name: /Collapse 1.2 Data Types/ })
    .click();
  await expect(tree.getByText("Structured Data")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Structured Data" }),
  ).toBeVisible();
  await chapter1.getByRole("button", { name: /Collapse Chapter 1/ }).click();
  await page.getByLabel("Find in book").fill("Authentication");
  await page.getByRole("button", { name: "Find", exact: true }).click();
  await expect(
    tree.getByRole("treeitem", { name: /Authentication/ }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(chapter2).toHaveAttribute("aria-expanded", "true");
  await expect(
    tree.getByRole("treeitem", { name: /2.2 Components/ }),
  ).toHaveAttribute("aria-expanded", "true");
  await expect(chapter1).toHaveAttribute("aria-expanded", "false");
  await expect(tree.getByText(/^Page \d+$/)).toHaveCount(0);
});

test("a real book shows its own nested contents and keeps the page window bounded", async ({
  page,
}) => {
  await page.goto("/");
  const docs = await (await page.request.get("/backend/api/documents")).json();
  // Pick the largest ready PDF in the library — the hardest real case.
  let target: { id: string; pages: number; depth: number } | null = null;
  for (const doc of docs.filter(
    (d: { kind: string; status: string }) =>
      d.kind === "pdf" && d.status === "ready",
  )) {
    const info = await (
      await page.request.get(`/backend/api/documents/${doc.id}/reader`)
    ).json();
    const pages = info.page_count || 0;
    const depth = Math.max(
      0,
      ...(info.outline || []).map((o: { level: number }) => o.level),
    );
    if (!target || pages > target.pages) target = { id: doc.id, pages, depth };
  }
  test.skip(!target || target.pages < 100, "no large PDF in this library");
  const book = target!;

  // The book's own table of contents, not a flat list of every heading.
  expect(book.depth).toBeGreaterThan(1);

  const freshSession = await (
    await page.request.post("/backend/api/sessions", {
      data: { document_id: book.id, new: true },
    })
  ).json();
  await page.goto(`/study/${book.id}?session=${freshSession.id}`);
  await expect(page.locator(".outline-tree")).toBeVisible();
  await expect(page.locator(".pdf-scroll .page").first()).toBeVisible();
  await expect(page.locator("#pdf-page-1 canvas")).toBeVisible();

  // Nearby pages stay warm. Scrolling down and back must not destroy page 1
  // and force the learner through the same loading state again.
  await page.locator('input[aria-label="Current page"]').fill("4");
  await expect(page.locator("#pdf-page-4 canvas")).toBeVisible();
  await page.waitForTimeout(500);
  await expect(page.locator("#pdf-page-1 canvas")).toHaveCount(1);
  await page.locator('input[aria-label="Current page"]').fill("1");
  await expect(page.locator("#pdf-page-1 canvas")).toBeVisible();

  // Collapsed by default: far fewer rows visible than the book has headings.
  const collapsed = await page.locator('[role="treeitem"]').count();
  expect(collapsed).toBeGreaterThan(0);

  // Expanding a chapter reveals its sections, indented one level deeper.
  const disclosure = page.locator(".tree-disclosure").first();
  await disclosure.click();
  await page.waitForTimeout(400);
  expect(await page.locator('[role="treeitem"]').count()).toBeGreaterThan(
    collapsed,
  );
  expect(
    await page.locator('[role="treeitem"][aria-level="2"]').count(),
  ).toBeGreaterThan(0);

  // Deep navigation keeps a bounded window mounted and exactly one active node.
  await page.locator('input[aria-label="Current page"]').fill("220");
  await page.waitForTimeout(5000);
  const mounted = await page.locator(".pdf-scroll canvas").count();
  expect(mounted).toBeGreaterThan(0);
  expect(mounted).toBeLessThanOrEqual(14);
  expect(await page.locator(".pdf-scroll .page").count()).toBe(book.pages);
  expect(
    await page.locator('.outline-tree [aria-selected="true"]').count(),
  ).toBe(1);
});
