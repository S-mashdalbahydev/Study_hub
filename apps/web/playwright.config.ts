import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  timeout: 240000,
  expect: { timeout: 30000 },
  workers: 1,
  use: {
    actionTimeout: 15000,
    baseURL: process.env.STUDY_WEB_URL || "http://127.0.0.1:3001",
    viewport: { width: 1440, height: 960 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  reporter: "list",
});
