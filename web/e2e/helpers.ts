// 浏览器端测试的共用函数。专属链接的秘密：用服务自己的命令行生成，只在内存里传给浏览器上下文的接口请求换会话（不经过地址栏，
// 所以不会出现在截图或调试记录里）。只有专门测试“打开链接后地址栏里的秘密被抹掉”的那一条才导航到 /invite#token=…。
import AxeBuilder from "@axe-core/playwright";
import { type Page, type TestInfo, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const REPO = path.resolve(here, "..", "..");
export const ORIGIN = "http://localhost:5180";
/** 截图与可访问性报告写在本机的 runs/web-e2e/（不进版本管理，每次运行覆盖）。 */
export const EVIDENCE = path.join(REPO, "runs", "web-e2e");
const PYTHON = path.join(REPO, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
/** serve-service.mjs 生成的运行时配置（数据目录是本仓库下的绝对路径）。 */
const CONFIG = path.join(REPO, "runs", "web-e2e", "service-e2e.runtime.yaml");
/** 服务从哪份代码起（与 serve-service.mjs 相同）：建链接的命令行必须用同一份数据目录。 */
const SERVICE_REPO = process.env.E5_SERVICE_REPO ? path.resolve(process.env.E5_SERVICE_REPO) : REPO;
const IDENTITY_FILE = path.join(EVIDENCE, ".e2e-identity-a.json");

export function evidencePath(...parts: string[]): string {
  const p = path.join(EVIDENCE, ...parts);
  mkdirSync(path.dirname(p), { recursive: true });
  return p;
}

/** 新建一个测试身份，返回专属链接里的秘密（只在内存里）。 */
export function createInvite(label: string): string {
  const out = execFileSync(PYTHON, ["scripts/product_service.py", "invite", "create", "--label", label, "--config", CONFIG], {
    cwd: SERVICE_REPO,
    encoding: "utf-8",
    env: { ...process.env, PYTHONIOENCODING: "utf-8" },
  });
  const line = out.split(/\r?\n/).find((l) => l.includes("#token="));
  if (!line) throw new Error("invite create 没有打印链接");
  return line.split("#token=")[1]?.trim() ?? "";
}

/** 用这个浏览器上下文的接口请求换会话（与页面共用 Cookie），再打开 path。 */
export async function signIn(page: Page, token: string, to = "/"): Promise<void> {
  const r = await page.request.post(`${ORIGIN}/api/v1/session`, {
    data: { invite_token: token },
    headers: { Origin: ORIGIN, "Content-Type": "application/json" },
  });
  expect(r.status(), "换会话").toBe(200);
  await page.goto(to);
}

/** 主闭环用的身份留给手机项目复用（只在本机的 runs/web-e2e/，手机项目结束时删掉）。 */
export function saveIdentityA(token: string, intentId: string): void {
  writeFileSync(evidencePath(".e2e-identity-a.json"), JSON.stringify({ token, intentId }), "utf-8");
}

export function loadIdentityA(): { token: string; intentId: string } {
  if (!existsSync(IDENTITY_FILE)) throw new Error("没有主闭环的身份：先跑桌面项目");
  return JSON.parse(readFileSync(IDENTITY_FILE, "utf-8"));
}

export function forgetIdentityA(): void {
  rmSync(IDENTITY_FILE, { force: true });
}

/** 截图写进 runs/web-e2e/screenshots/<项目>/<名字>.png。 */
export async function shot(page: Page, info: TestInfo, name: string): Promise<void> {
  await page.screenshot({ path: evidencePath("screenshots", info.project.name, `${name}.png`), fullPage: true });
}

export async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, "页面出现了横向滚动").toBeLessThanOrEqual(1);
}

/** axe：严重、致命、中等的问题都要是 0；报告写进 runs/web-e2e/a11y/。 */
export async function expectAccessible(page: Page, info: TestInfo, name: string): Promise<void> {
  const result = await new AxeBuilder({ page }).analyze();
  const bad = result.violations.filter((v) => ["critical", "serious", "moderate"].includes(v.impact ?? ""));
  writeFileSync(
    evidencePath("a11y", info.project.name, `${name}.json`),
    JSON.stringify(
      bad.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.map((n) => n.target) })),
      null,
      1,
    ),
    "utf-8",
  );
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`), `axe：${name}`).toEqual([]);
}

/** 等草稿保存完（页面上的保存状态是“已保存”）。 */
export async function waitSaved(page: Page): Promise<void> {
  await expect(page.locator('[data-save="saved"]')).toBeVisible({ timeout: 30_000 });
}

/** 数一类请求（不读内容，不记录任何秘密）。 */
export function countRequests(page: Page, test: (url: string, method: string) => boolean): { get(): number } {
  let n = 0;
  page.on("request", (r) => {
    if (test(r.url(), r.method())) n += 1;
  });
  return { get: () => n };
}
