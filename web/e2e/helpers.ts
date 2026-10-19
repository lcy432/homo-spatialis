import { mkdirSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { type Page, type TestInfo, expect } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
/** 截图、导出的草稿与可访问性报告写在仓库的 runs/web-e2e/（运行输出，不进版本管理，每次运行覆盖）。 */
export const EVIDENCE = path.resolve(here, "..", "..", "runs", "web-e2e");

const SAMPLES = path.resolve(here, "..", "..", "contracts", "product", "v2", "samples");

/** 一份公共样例里的运行编号。从样例文件读，E3 重新生成样例（运行编号随之改变）时不用改测试。 */
export function sampleRunId(sampleId: string): string {
  const file = readdirSync(SAMPLES).find((f) => f.startsWith(`${sampleId}_`));
  if (!file) throw new Error(`没有编号为 ${sampleId} 的公共样例`);
  const runId = JSON.parse(readFileSync(path.join(SAMPLES, file), "utf-8")).payload?.run_id;
  if (typeof runId !== "string") throw new Error(`样例 ${sampleId} 没有运行编号`);
  return runId;
}

/** 正常闭环（样例 07–10）的运行编号。 */
export const NORMAL_RUN = sampleRunId("10");

export function evidencePath(...parts: string[]): string {
  const p = path.join(EVIDENCE, ...parts);
  mkdirSync(path.dirname(p), { recursive: true });
  return p;
}

/** 截图写进证据目录：screenshots/<尺寸>/<名字>.png */
export async function shot(page: Page, info: TestInfo, name: string): Promise<void> {
  await page.screenshot({ path: evidencePath("screenshots", info.project.name, `${name}.png`), fullPage: true });
}

/** 没有横向滚动条（手机尺寸的硬要求）。 */
export async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, "页面出现了横向滚动").toBeLessThanOrEqual(1);
}

/** 打开一个页面并把网络模拟的延迟调成 0、先清空本标签页的存储。 */
export async function fresh(page: Page, url = "/fill/1"): Promise<void> {
  await page.goto(url);
  await page.evaluate(() => sessionStorage.clear());
  await page.goto(url);
  await setTransport(page, { latencyMs: 0, offline: false, dropNextReceipt: false });
}

export async function setTransport(
  page: Page,
  patch: Partial<{ latencyMs: number; offline: boolean; dropNextReceipt: boolean }>,
): Promise<void> {
  await page.waitForFunction(() => !!window.__E5A__);
  await page.evaluate((p) => window.__E5A__?.setTransport(p), patch);
}

export async function submissions(page: Page): Promise<{ idempotencyKey: string; replied: string }[]> {
  return page.evaluate(() => (window.__E5A__?.adapter.submissions ?? []).map((s) => ({ idempotencyKey: s.idempotencyKey, replied: s.replied })));
}

/** 只用键盘：按 Tab 直到焦点落在符合条件的元素上，然后返回。 */
export async function tabTo(page: Page, matcher: (el: { tag: string; text: string; label: string | null; name: string | null }) => boolean, max = 200): Promise<void> {
  for (let i = 0; i < max; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el) return null;
      return {
        tag: el.tagName.toLowerCase(),
        text: (el.innerText || el.textContent || "").trim(),
        label: el.getAttribute("aria-label"),
        name: el.getAttribute("name"),
      };
    });
    if (info && matcher(info)) return;
  }
  throw new Error("按 Tab 没有走到目标元素");
}
