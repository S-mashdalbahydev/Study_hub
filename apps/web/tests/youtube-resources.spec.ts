import { test, expect } from "@playwright/test";

/**
 * The resource picker is fed by live YouTube discovery, so the rows are stubbed here.
 * What this protects is the rendering contract, not the network:
 *  - a playlist shows its first video's thumbnail and how many videos it holds
 *  - a single video shows a thumbnail but no misleading "1 videos" pill
 *  - an article shows neither, and never a broken image
 *  - every row still links to the real URL that was validated
 */

// Production stores the unit title on each resource and the numbered form in the
// outline, so the fixture mirrors that exactly.
const CHAPTER = "Storage";

function resource(over: Record<string, unknown>) {
  return {
    id: "R" + Math.random().toString(16).slice(2, 12),
    chapter: CHAPTER,
    title: "untitled",
    url: "https://example.com",
    resource_type: "article_or_demo",
    publisher: "example.com",
    snippet: "",
    validation_reason: "Fetched a real, readable article matching this unit.",
    selected: false,
    thumbnail: "",
    video_count: null,
    ...over,
  };
}

const JOB = {
  id: "stubjob",
  mode: "companion",
  material_type: "learning_companion",
  source_document_id: "doc",
  query: "Learning Companion: Storage",
  urls: [],
  depth: "quick",
  level: "intermediate",
  goal: "learn storage",
  status: "review",
  progress: { message: "Ready for review", completed: 3, total: 3 },
  outline: ["Unit 1: Storage"],
  draft: "# Draft\n\n## Unit 1: Storage\n\nSome text.",
  error: null,
  document_id: null,
  sources: [],
  resources: [
    resource({
      title: "C Programming Tutorials",
      url: "https://www.youtube.com/playlist?list=PL_c9BZzLwBRKKqOc9TJz1pP0ASrxLMtp2",
      resource_type: "playlist_en",
      publisher: "youtube.com",
      thumbnail: "https://i.ytimg.com/vi/Bz4MxDeEM6k/hqdefault.jpg",
      video_count: 101,
      validation_reason:
        "Confirmed a public YouTube playlist with 101 videos matching this unit.",
    }),
    resource({
      title: "شرح قواعد البيانات",
      url: "https://www.youtube.com/playlist?list=PLarabic",
      resource_type: "playlist_ar",
      publisher: "youtube.com",
      thumbnail: "https://i.ytimg.com/vi/1ukSR1GRtMU/hqdefault.jpg",
      video_count: 36,
      validation_reason:
        "Confirmed a public YouTube playlist with 36 videos matching this unit.",
    }),
    resource({
      title: "Pointers in C",
      url: "https://www.youtube.com/watch?v=Bz4MxDeEM6k",
      resource_type: "video",
      publisher: "Caleb Curry",
      thumbnail: "https://i.ytimg.com/vi/Bz4MxDeEM6k/hqdefault.jpg",
      video_count: 1,
      validation_reason:
        "YouTube confirmed this is a public video by Caleb Curry, matching this unit.",
    }),
    resource({
      title: "Storage Engines Explained",
      url: "https://example.com/storage-engines",
    }),
  ],
};

test.beforeEach(async ({ page }) => {
  // Thumbnails are served by YouTube; keep the test off the network.
  await page.route("https://i.ytimg.com/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "image/gif",
      body: Buffer.from(
        "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",
        "base64",
      ),
    }),
  );
  await page.route("**/backend/api/research", (route) =>
    route.fulfill({ status: 200, json: [JOB] }),
  );
  await page.route("**/backend/api/research/stubjob", (route) =>
    route.fulfill({ status: 200, json: JOB }),
  );
});

async function openPicker(
  page: import("@playwright/test").Page,
  label: RegExp,
) {
  await page.goto("/");
  await page.getByRole("button", { name: /^Open research:/ }).click();
  // Scope to this unit's row: the same chips repeat for every unit.
  const row = page
    .locator(".resource-unit-row")
    .filter({ has: page.getByText(CHAPTER, { exact: true }) });
  await row.locator(".resource-chip").filter({ hasText: label }).click();
  await expect(page.locator(".resource-picker-list li").first()).toBeVisible();
}

test("a playlist shows its first video and how many videos it holds", async ({
  page,
}) => {
  await openPicker(page, /English playlists/);
  const row = page.locator(".resource-picker-list li").first();

  const thumb = row.locator("img.resource-thumb");
  await expect(thumb).toHaveAttribute(
    "src",
    "https://i.ytimg.com/vi/Bz4MxDeEM6k/hqdefault.jpg",
  );
  // A thumbnail that fails to load is worse than none at all.
  await expect
    .poll(async () => thumb.evaluate((i: HTMLImageElement) => i.naturalWidth))
    .toBeGreaterThan(0);

  await expect(row.locator(".resource-count")).toHaveText("101 videos");
  await expect(
    row.getByRole("link", { name: "C Programming Tutorials" }),
  ).toHaveAttribute(
    "href",
    "https://www.youtube.com/playlist?list=PL_c9BZzLwBRKKqOc9TJz1pP0ASrxLMtp2",
  );
});

test("an Arabic playlist renders its own title and count", async ({ page }) => {
  await openPicker(page, /Arabic playlists/);
  const row = page.locator(".resource-picker-list li").first();
  await expect(row).toContainText("شرح قواعد البيانات");
  await expect(row.locator(".resource-count")).toHaveText("36 videos");
  await expect(row.locator("img.resource-thumb")).toBeVisible();
});

test("a single video shows a thumbnail but no misleading count", async ({
  page,
}) => {
  await openPicker(page, /Videos/);
  const row = page.locator(".resource-picker-list li").first();
  await expect(row.locator("img.resource-thumb")).toBeVisible();
  await expect(row.locator(".resource-count")).toHaveCount(0);
  await expect(row).toContainText("Caleb Curry");
});

test("an article shows no thumbnail and no count", async ({ page }) => {
  await openPicker(page, /Articles/);
  const row = page.locator(".resource-picker-list li").first();
  await expect(row.locator("img.resource-thumb")).toHaveCount(0);
  await expect(row.locator(".resource-count")).toHaveCount(0);
  await expect(row).toContainText("Storage Engines Explained");
});

test("selecting a playlist sends its id and reflects the new state", async ({
  page,
}) => {
  const playlist = JOB.resources[0];
  let sent: { url: string; body: unknown } | null = null;
  await page.route("**/resources/*/selection", async (route) => {
    sent = {
      url: route.request().url(),
      body: route.request().postDataJSON(),
    };
    await route.fulfill({
      status: 200,
      json: {
        ...JOB,
        resources: JOB.resources.map((r) =>
          r.id === playlist.id ? { ...r, selected: true } : r,
        ),
      },
    });
  });

  await openPicker(page, /English playlists/);
  await page
    .locator(".resource-picker-list input[type=checkbox]")
    .first()
    .click();

  await expect.poll(() => sent).not.toBeNull();
  expect(sent!.url).toContain(`/resources/${playlist.id}/selection`);
  expect(sent!.body).toEqual({ selected: true });
  await expect(
    page.locator(".resource-picker-list input[type=checkbox]").first(),
  ).toBeChecked();
});
