import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// Model and study mode live in the Tutor options popover.
async function tutorOptions(page: import("@playwright/test").Page) {
  const panel = page.getByRole("region", { name: "Tutor" });
  if (!(await panel.isVisible()))
    await page.getByRole("button", { name: "Tutor", exact: true }).click();
  await page.getByRole("button", { name: "Tutor options" }).click();
}
test("real PDF upload → reader → local stream → citation → saved Continue", async ({
  page,
  context,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Your library" }),
  ).toBeVisible();
  const fixture = Buffer.concat([
    fs.readFileSync(path.resolve("../../examples/helios-workshop.pdf")),
    Buffer.from("\n% StudyHub frontend verification fixture v1\n"),
  ]);
  const transfer = await page.evaluateHandle((bytes) => {
    const dt = new DataTransfer();
    dt.items.add(
      new File([new Uint8Array(bytes)], "workspace-check.pdf", {
        type: "application/pdf",
      }),
    );
    return dt;
  }, Array.from(fixture));
  const uploaded = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/documents/upload") &&
      r.request().method() === "POST",
  );
  await page.locator("main").dispatchEvent("drop", { dataTransfer: transfer });
  const upload = await (await uploaded).json();
  expect(upload.warnings).toEqual([]);
  const id = upload.document_ids[0];
  await expect(page.getByText("Upload complete")).toBeVisible();
  await expect
    .poll(
      async () => {
        const r = await page.request.get(`/backend/api/documents/${id}`);
        return ["ready", "failed"].includes((await r.json()).status);
      },
      { timeout: 150000 },
    )
    .toBe(true);
  const initial = await (
    await page.request.get(`/backend/api/documents/${id}`)
  ).json();
  if (initial.status === "failed")
    await page
      .getByRole("button", { name: "Refresh library", exact: true })
      .first()
      .click();
  await expect
    .poll(
      async () => {
        const r = await page.request.get(`/backend/api/documents/${id}`);
        return (await r.json()).status;
      },
      { timeout: 150000 },
    )
    .toBe("ready");
  const document = await (
    await page.request.get(`/backend/api/documents/${id}`)
  ).json();
  const cover = page
    .locator(".doc-card", { hasText: document.title })
    .locator(".cover img")
    .first();
  await expect(cover).toBeVisible();
  await expect
    .poll(() => cover.evaluate((img: HTMLImageElement) => img.naturalWidth))
    .toBeGreaterThan(0);
  await page.screenshot({
    path: "test-results/library-dark.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: /^Appearance/ }).click();
  await page.getByRole("menuitemradio", { name: "Light theme" }).click();
  await page.waitForTimeout(300);
  await page.screenshot({
    path: "test-results/library-light.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: /^Appearance/ }).click();
  await page.getByRole("menuitemradio", { name: "Dark theme" }).click();
  await page
    .getByRole("button", {
      name: new RegExp(`^(Open|Continue) ${document.title}$`),
    })
    .first()
    .click();
  await expect(page).toHaveURL(new RegExp(`/study/${id}`));
  // The reader renders a window of nearby pages, so several canvases are expected.
  await expect(page.locator(".pdf-scroll canvas").first()).toBeVisible();
  await expect(
    page.getByRole("complementary", { name: "Document outline" }),
  ).toBeVisible();
  await tutorOptions(page);
  const options = await page.locator("#model option").evaluateAll((items) =>
    items.map((x) => ({
      value: (x as HTMLOptionElement).value,
      text: x.textContent || "",
    })),
  );
  const local = options.find((x) => x.text.startsWith("Local"));
  expect(local).toBeTruthy();
  await page.getByLabel("Tutor model").selectOption(local!.value);
  await page.getByLabel("Study mode").selectOption("practical");
  await page.keyboard.press("Escape");
  await page
    .getByRole("textbox", { name: "Ask about this document" })
    .fill(
      "What color is the reference tile, and how many minutes must the operator wait?",
    );
  await page.getByRole("button", { name: "Send message", exact: true }).click();

  await expect(page.getByTestId("assistant-message").last()).toContainText(
    "cobalt",
    { timeout: 150000 },
  );
  await expect(page.getByTestId("assistant-message").last()).toContainText(
    "17",
  );
  await expect(page.getByTestId("citation").last()).toBeVisible({
    timeout: 150000,
  });
  await page.getByRole("button", { name: /^View/ }).click();
  await page.getByRole("menuitemradio", { name: /Tutor only/ }).click();
  await page
    .getByTestId("citation")
    .filter({ hasText: "Page 2" })
    .last()
    .click();
  await expect(
    page.getByRole("spinbutton", { name: "Current page" }),
  ).toHaveValue("2");
  await page.screenshot({
    path: "test-results/workspace-desktop.png",
    fullPage: true,
  });
  const url = page.url();
  await expect
    .poll(async () => {
      const sid = new URL(url).searchParams.get("session");
      const r = await page.request.get(`/backend/api/sessions/${sid}`);
      return (await r.json()).page;
    })
    .toBe(2);
  await page.reload();
  await expect(
    page.getByRole("spinbutton", { name: "Current page" }),
  ).toHaveValue("2");
  await tutorOptions(page);
  await expect(page.getByLabel("Tutor model")).toHaveValue(local!.value);
  await expect(page.getByLabel("Study mode")).toHaveValue("practical");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("assistant-message").last()).toContainText(
    "cobalt",
  );
  await page.getByRole("link", { name: "Back to library" }).click();
  await expect(
    page.getByRole("heading", { name: "Continue reading" }),
  ).toBeVisible();
  await page.close();
  const reopened = await context.newPage();
  await reopened.goto("/");
  await reopened
    .getByRole("button", { name: `Continue ${document.title}`, exact: true })
    .first()
    .click();
  await expect(reopened).toHaveURL(url);
  await expect(
    reopened.getByRole("spinbutton", { name: "Current page" }),
  ).toHaveValue("2");
  await reopened.setViewportSize({ width: 390, height: 844 });
  await reopened.screenshot({
    path: "test-results/workspace-mobile.png",
    fullPage: true,
  });
  await expect
    .poll(() =>
      reopened.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    )
    .toBe(true);
  await reopened.getByRole("tab", { name: "Tutor" }).click();
  await expect(
    reopened.getByRole("textbox", { name: "Ask about this document" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
