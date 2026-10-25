import { defineConfig, devices } from "@playwright/test";

// 浏览器端测试接真实的本地服务：Playwright 先起隔离的 E3C 服务（接口 + worker，127.0.0.1:8775，离线 mock，数据目录每次清空），
// 再起 Vite（5180，/api 代理到 8775），页面来源 http://localhost:5180 与服务配置的 site_origin 完全一致。
// 桌面项目先跑（含完整的模拟主闭环）；手机项目依赖它，只跑标了 @mobile 的测试（标 @mobile-only 的只在手机上跑），复用桌面
// 跑出来的那次运行查看结果。
// 截图、可访问性报告写进本机的 runs/web-e2e/（runs/ 不进 git，每次覆盖）；调试记录只在失败时保留在 test-results/。
const PORT = 5180;
const API = "http://127.0.0.1:8775";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "./test-results",
  globalTeardown: "./e2e/global-teardown.ts",
  timeout: 120_000,
  use: {
    baseURL: `http://localhost:${PORT}`,
    locale: "zh-CN",
    trace: "retain-on-failure",
    // 无头浏览器里的地球用软件 3D 渲染（没有显卡时也能跑）。页面本身用 CPU 栅格化：软件 GPU 也拿去栅格化页面时，
    // 带大地图的页面整页截图会随机缺块（透明的横条、卡片断开）；同样的页面用 CPU 栅格化每次都完整。
    launchOptions: {
      args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader", "--ignore-gpu-blocklist", "--disable-gpu-rasterization"],
    },
  },
  projects: [
    {
      name: "desktop",
      grepInvert: /@mobile-only/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } },
    },
    {
      name: "mobile",
      dependencies: ["desktop"],
      grep: /@mobile/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 360, height: 740 }, hasTouch: true, isMobile: false },
    },
  ],
  webServer: [
    {
      command: "node e2e/serve-service.mjs",
      url: `${API}/api/v1/session`,
      reuseExistingServer: false,
      timeout: 180_000,
      stdout: "pipe",
    },
    {
      command: `npx vite --port ${PORT} --strictPort`,
      url: `http://localhost:${PORT}/`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { E5_API_TARGET: API, ...(process.env.E5_CONTRACTS_DIR ? { E5_CONTRACTS_DIR: process.env.E5_CONTRACTS_DIR } : {}) },
    },
  ],
});
