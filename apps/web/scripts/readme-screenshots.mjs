// Regenerates the README screenshots against a running stack:
//   node scripts/readme-screenshots.mjs   (STUDY_WEB_URL defaults to http://127.0.0.1:3001)
// It signs up a throwaway demo account filled only with the synthetic, CC0 material in
// examples/ and docs/demo/, so no personal or copyrighted content reaches the images.
import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.STUDY_WEB_URL || "http://127.0.0.1:3001";
const ROOT = path.resolve(import.meta.dirname, "../../..");
const OUT = path.join(ROOT, "docs/images");
const EMAIL = process.env.DEMO_EMAIL || `demo-${Date.now()}@studyhub.example`;
const PASSWORD = "a demo password for screenshots";
const CSRF = { "X-StudyHub-CSRF": "1" };
const SOURCES = [
  ["examples/helios-workshop.pdf", "application/pdf"],
  ["examples/river-garden.pdf", "application/pdf"],
  ["docs/demo/water-cycle-field-guide.md", "text/markdown"],
  ["docs/demo/plants-and-water.md", "text/markdown"],
];

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, baseURL: BASE });
const page = await context.newPage();
const theme = (value) => page.evaluate((v) => localStorage.setItem("theme", v), value);
const shot = async (name, options = {}) => {
  await page.waitForTimeout(600); // let fonts, covers and transitions settle
  await page.screenshot({ path: path.join(OUT, `${name}.png`), ...options });
  console.log("saved", name);
};
const api = async (method, url, data) => {
  const response = await page.request.fetch(`/backend${url}`, { method, headers: CSRF, data });
  if (!response.ok()) throw new Error(`${method} ${url}: ${response.status()} ${await response.text()}`);
  return response.status() === 204 ? null : response.json();
};

// Logged out: the front page is the sign-up page.
await page.goto("/");
await theme("light");
await page.reload();
await shot("signup-light");
await theme("dark");
await page.reload();
await shot("signup-dark");
await page.goto("/login");
await shot("login");

// Sign up through the real form.
await page.goto("/signup");
await page.locator("#auth-name").fill("Demo Reader");
await page.locator("#auth-email").fill(EMAIL);
await page.locator("#auth-password").fill(PASSWORD);
await page.getByRole("button", { name: "Create account" }).click();
await page.waitForURL(BASE + "/");

const ids = {};
for (const [file, type] of SOURCES) {
  const response = await page.request.post("/backend/api/documents/upload", {
    headers: CSRF,
    multipart: { file: { name: path.basename(file), mimeType: type, buffer: fs.readFileSync(path.join(ROOT, file)) } },
  });
  ids[path.basename(file)] = (await response.json()).document_ids[0];
}
for (let i = 0; i < 120; i++) {
  const docs = await api("GET", "/api/documents");
  if (docs.length === SOURCES.length && docs.every((d) => ["ready", "failed"].includes(d.status))) break;
  await page.waitForTimeout(2000);
}

await page.goto("/");
await page.locator(".doc-card").first().waitFor();
await shot("library");

// A generated-style Markdown book opens as a paginated digital book.
await page.goto(`/study/${ids["water-cycle-field-guide.md"]}`);
await page.waitForTimeout(2500);
await shot("reader-book");

// The original PDF beside the tutor, answering with a page citation.
await page.goto(`/study/${ids["helios-workshop.pdf"]}`);
await page.locator(".pdf-scroll .page").first().waitFor({ timeout: 60000 }).catch(() => {});
const panel = page.getByRole("region", { name: "Tutor" });
if (!(await panel.isVisible().catch(() => false)))
  await page.getByRole("button", { name: "Tutor", exact: true }).click().catch(() => {});
await page.getByLabel("Ask about this document").fill("How long does the operator wait before calibration?");
await page.getByLabel("Send message").click();
await page.getByTestId("citation").first().waitFor({ timeout: 240000 }).catch(() => console.log("no citation within 4 minutes"));
await shot("reader-chat");

// A study space across two guides, with themes typed by the learner (no model needed).
const space = await api("POST", "/api/learning-sessions", {
  title: "Water, from sky to leaf",
  learning_goal: "Understand how water moves through the environment and through plants",
  document_ids: [ids["water-cycle-field-guide.md"], ids["plants-and-water.md"]],
});
await api("POST", `/api/learning-sessions/${space.id}/generate`, {
  themes: ["Water in the atmosphere", "Water in soil and plants"],
}).catch((e) => console.log("study map:", e.message));
await page.goto(`/sessions/${space.id}`);
await page.waitForTimeout(2000);
await shot("study-space");

// Research: turn a topic into a reviewed, cited study book.
await page.goto("/");
await page.getByRole("button", { name: "Add or create" }).click();
await page.getByRole("menuitem", { name: /Research a topic/ }).click();
await page.getByPlaceholder(/Apache Iceberg/).fill("How rainforests create their own rain");
await shot("research");

await page.goto("/settings");
await page.waitForTimeout(1500);
await shot("settings");

console.log("demo account:", EMAIL);
await browser.close();
