import { defineConfig, devices } from "@playwright/test";

// 端到端检查：Playwright 自己起开发服务器（端口 5180，避免与手动打开的 5173 冲突）。
// 两种尺寸：手机 360×740、桌面 1280×800。截图与导出的草稿写进证据目录 runs/2026-10-18-e5-a-offline-ui/。
const PORT = 5180;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "./test-results",
  use: {
    baseURL: `http://localhost:${PORT}`,
    locale: "zh-CN",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "mobile",
      use: { ...devices["Desktop Chrome"], viewport: { width: 360, height: 740 }, hasTouch: true, isMobile: false },
    },
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } },
    },
  ],
  webServer: {
    command: `npx vite --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}/fill/1`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
