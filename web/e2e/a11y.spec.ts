import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { NORMAL_RUN, evidencePath, fresh } from "./helpers";

test("关键页面没有可访问性问题（axe：严重、致命、中等都是 0）", async ({ page }, info) => {
  await fresh(page, "/fill/1");
  await page.getByRole("button", { name: "载入样例用户 u501 的答案" }).click();
  const pages = ["/fill/1", "/fill/2", "/fill/3", "/fill/4", `/runs/${NORMAL_RUN}`, `/runs/${NORMAL_RUN}/result`];
  const report: Record<string, unknown[]> = {};
  for (const url of pages) {
    await page.goto(url);
    await page.locator("#page-title").waitFor();
    if (url === "/fill/4") await page.getByRole("heading", { name: "系统怎样理解（E3 审阅）" }).waitFor();
    if (url.endsWith("/result")) await page.locator("[data-city]").first().waitFor();
    const results = await new AxeBuilder({ page }).analyze();
    report[url] = results.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, help: v.help }));
    // 严重、致命与中等问题都要为 0（轻微问题记录在证据里）
    const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical" || v.impact === "moderate");
    expect(blocking, `${url}: ${blocking.map((v) => `${v.id}（${v.help}）`).join("；")}`).toEqual([]);
  }
  writeFileSync(evidencePath("a11y", `${info.project.name}.json`), JSON.stringify(report, null, 2));
});
