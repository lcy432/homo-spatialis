import { realpathSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 共享结构与样例在仓库的 contracts/product/v3/（由 E3 维护），在 web/ 之外：开发服务器要允许读取仓库根目录。
// 别名 @contracts 默认指向它；E5_CONTRACTS_DIR 可以指向另一份（例如只含已提交代码的干净工作树），让页面、契约核对与服务说同一个版本。
const repoRoot = fileURLToPath(new URL("..", import.meta.url));
// 用真实的长路径（Windows 的 8.3 短名会让模块路径对不上）
const contractsDir = realpathSync.native(
  process.env.E5_CONTRACTS_DIR ? path.resolve(process.env.E5_CONTRACTS_DIR) : path.join(repoRoot, "contracts", "product", "v3"),
);

// /api 同源代理到 E3C 服务：浏览器始终只看到页面这一个来源，Cookie 与 Origin 检查按同源工作（不放宽 CORS）。
// 本地联调默认是 configs/product/service_local_mock.yaml 的 127.0.0.1:8765；浏览器测试用 E5_API_TARGET 指向自己的隔离服务。
const apiTarget = process.env.E5_API_TARGET ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@contracts": contractsDir } },
  server: {
    port: 5173,
    strictPort: true,
    fs: { allow: [repoRoot, contractsDir] },
    proxy: { "/api": { target: apiTarget, changeOrigin: false } },
  },
  preview: {
    proxy: { "/api": { target: apiTarget, changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
