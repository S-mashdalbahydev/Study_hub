import { test, expect } from "@playwright/test";

test("Learning Companion review uses visual units and bilingual resources", async ({
  page,
}) => {
  await page.goto("/");
  // This asserts on live SearXNG/YouTube discovery, so it can only run against a
  // companion parked in review. Skipping beats asserting on third-party results.
  const jobs = await (await page.request.get("/backend/api/research")).json();
  const reviewing = jobs.find(
    (job: { material_type: string; status: string }) =>
      job.material_type === "learning_companion" && job.status === "review",
  );
  test.skip(
    !reviewing,
    "no Learning Companion is awaiting review in this environment",
  );
  await page
    .getByRole("button", { name: `Open research: ${reviewing.query}` })
    .click();
  await expect(page.locator(".draft .study-unit").first()).toBeVisible();
  await expect(page.locator(".draft .study-section").first()).toBeVisible();
  // Playlists are chosen per unit, in both languages, beside the draft.
  const row = page.locator(".resource-unit-row").first();
  await expect(
    row.locator(".resource-chip", { hasText: "English playlists" }),
  ).toBeVisible();
  await expect(
    row.locator(".resource-chip", { hasText: "Arabic playlists" }),
  ).toBeVisible();
});

test("Markdown documents render as structured, navigable study material", async ({
  page,
}) => {
  const docs = await (await page.request.get("/backend/api/documents")).json();
  const doc = docs.find(
    (d: { status: string; kind: string }) =>
      d.status === "ready" && d.kind === "md",
  );
  expect(doc).toBeTruthy();
  const reader = await (
    await page.request.get(`/backend/api/documents/${doc.id}/reader`)
  ).json();
  const section = reader.book.toc.find(
    (item: { kind: string }) => item.kind === "chapter",
  );
  expect(section).toBeTruthy();
  // The outline shows titles as words, without a guide's decorative emoji.
  const title = section.title.replace(
    /^[\s\p{Extended_Pictographic}\uFE0F\u200D]+/u,
    "",
  );
  const session = await (
    await page.request.post("/backend/api/sessions", {
      data: { document_id: doc.id, new: true },
    })
  ).json();

  await page.goto(`/study/${doc.id}?session=${session.id}`);
  await expect(
    page
      .locator(".book-cover-copy")
      .getByRole("heading", { name: reader.book.title }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Start reading/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Start reading/ }).click();
  await expect(page.locator(".reader-text h1").first()).toBeVisible();
  await expect(page.getByText(/Page 2 of/).first()).toBeVisible();

  const tree = page.getByRole("tree", { name: "Book outline" });
  const chapterItem = tree.getByRole("treeitem", {
    name: new RegExp(title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
  });
  await expect(chapterItem).toHaveAttribute("aria-expanded", "false");
  await chapterItem.getByRole("button", { name: `Expand ${title}` }).click();
  await expect(chapterItem).toHaveAttribute("aria-expanded", "true");

  const outlineButton = page
    .getByRole("complementary", { name: "Document outline" })
    .locator(".tree-label")
    .filter({ hasText: title });
  await outlineButton.click();
  await expect(page.locator(`#${section.id}`)).toBeInViewport();
  await expect(page.locator(".book-sheet")).toHaveCount(1);
});

test("selected PDF text reaches the tutor; Stop persists a cancelled reply", async ({
  page,
}) => {
  const docs = await (await page.request.get("/backend/api/documents")).json();
  const doc = docs.find(
    (d: { status: string; kind: string }) =>
      d.status === "ready" && d.kind === "pdf",
  );
  expect(doc).toBeTruthy();
  const session = await (
    await page.request.post("/backend/api/sessions", {
      data: { document_id: doc.id, new: true },
    })
  ).json();
  await page.goto(`/study/${doc.id}?session=${session.id}`);
  await expect(page.locator(".pdf-scroll .textLayer").first()).toBeVisible();
  // The text layer element appears before its spans are filled in.
  await page
    .locator(".pdf-scroll .textLayer span")
    .first()
    .waitFor({ state: "attached", timeout: 30000 });
  await page.getByRole("button", { name: "Tutor options" }).click();
  const local = await page.locator("#model option").evaluateAll((options) =>
    options
      .map((o) => ({
        value: (o as HTMLOptionElement).value,
        text: o.textContent || "",
      }))
      .find((o) => o.text.startsWith("Local")),
  );
  expect(local).toBeTruthy();
  await page.getByLabel("Tutor model").selectOption(local!.value);
  await page.keyboard.press("Escape");
  const selected = await page.locator(".pdf-scroll").evaluate((holder) => {
    const span = Array.from(
      holder.querySelectorAll(".pdf-scroll .textLayer span"),
    ).find((s) => s.textContent?.trim());
    if (!span) throw new Error("No selectable PDF text");
    const range = document.createRange();
    range.selectNodeContents(span);
    const selection = getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    holder.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    return selection.toString();
  });
  await expect(
    page.getByText("Selected passage", { exact: true }),
  ).toBeVisible();
  const sent = page.waitForRequest((r) =>
    r.url().endsWith(`/sessions/${session.id}/chat`),
  );
  await page.getByRole("button", { name: "Simplify", exact: true }).click();
  const request = await sent;
  expect(request.postDataJSON()).toMatchObject({
    selected_text: selected,
    intent: "simplify",
  });
  await expect(page.getByTestId("citation").first()).toBeVisible({
    timeout: 150000,
  });
  await page
    .getByRole("textbox", { name: "Ask about this document" })
    .fill(
      "Explain every detail of the current page in at least 1000 words, with a detailed example for each sentence.",
    );
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Stop generation" }),
  ).toBeVisible();
  await expect
    .poll(() => page.getByTestId("assistant-message").last().innerText(), {
      timeout: 150000,
    })
    .toMatch(/.{40}/s);
  await page.getByRole("button", { name: "Stop generation" }).click();
  await expect(page.getByText("Stopped", { exact: true })).toBeVisible();
  await expect
    .poll(
      async () => {
        const s = await (
          await page.request.get(`/backend/api/sessions/${session.id}`)
        ).json();
        return s.messages.at(-1).status;
      },
      { timeout: 15000 },
    )
    .toBe("cancelled");
  await page.reload();
  await expect(page.getByText("Stopped", { exact: true })).toBeVisible();
});
