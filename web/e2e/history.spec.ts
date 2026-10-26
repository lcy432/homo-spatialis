// 历史结果的地图版本（E5-R4）：结果页先读这份结果的显示信封，把它绑定的 resource_version 传给地理清单。
// * 版本为空：不发任何地理请求（省略版本 = 当前登记的版本，不是历史版本），页面写明“没有绑定的历史版本”，不画城市点，表格照常。
// * 版本是一个不存在的旧版本：带版本请求 404，不回退到不带版本的请求，不画点。
// 用受控注入：意图、结果与结果显示信封来自公共样例（合成资料包，不是另一次真实模拟）；地理请求走真实服务。
import { expect, test } from "@playwright/test";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { REPO, createInvite, shot, signIn } from "./helpers";

const DIR = path.join(REPO, "contracts", "product", "v3", "samples");
const sample = (prefix: string) => JSON.parse(readFileSync(path.join(DIR, readdirSync(DIR).find((x) => x.startsWith(`${prefix}_`)) as string), "utf8")).payload;

async function openInjected(page: import("@playwright/test").Page, resourceVersion: string | null, label: string) {
  const intent = sample("78");
  const result = sample("104");
  const envelope = sample("116");
  intent.run_id = result.run_id;
  intent.result_available = true;
  envelope.id = result.run_id;
  envelope.source = result;
  envelope.resource_version = resourceVersion;
  envelope.fields = [];
  envelope.status = resourceVersion ? "partial" : "unavailable";
  const geographyCalls: string[] = [];
  await page.route("**/api/v1/run-intents/*", (route) => route.fulfill({ json: intent }));
  await page.route("**/api/v1/runs/*/result", (route) => route.fulfill({ json: result }));
  await page.route("**/api/v1/display?*", (route) => {
    const kind = new URL(route.request().url()).searchParams.get("kind");
    if (kind === "result") return route.fulfill({ json: envelope });
    if (kind === "intent") return route.fulfill({ status: 404, json: { code: "not_found", status: 404, message_zh: "没有", impact_zh: "无", next_action_zh: "无" } });
    return route.continue();
  });
  page.on("request", (r) => {
    if (r.url().includes("/display/geography?")) geographyCalls.push(r.url());
  });
  await signIn(page, createInvite(label), `/results/${intent.intent_id}`);
  await expect(page.locator("[data-results]")).toBeVisible({ timeout: 60_000 });
  return geographyCalls;
}

test("历史结果没有绑定的版本：没有任何地理请求、不画城市点，表格与底图照常", async ({ page }, info) => {
  const calls = await openInjected(page, null, "e2e 历史 无版本");
  await expect(page.locator('[data-geo-version="unbound"]')).toBeVisible();
  await expect(page.locator('table[data-table="cities"] tbody tr')).toHaveCount(6);
  await expect(page.locator('[data-map="paper"] [data-country]').first()).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1500);
  await shot(page, info, "history-unbound");
  expect(calls).toEqual([]);
  await expect(page.locator('[data-map="paper"] [data-anchor]')).toHaveCount(0);
  await expect(page.locator('[data-anchors="unavailable"]')).toBeVisible();
  // 切到地球模式：同样不请求当前版本
  await page.locator('[data-toggle="mode-globe"]').click();
  await expect(page.locator("html")).toHaveAttribute("data-mode", "globe");
  await page.waitForTimeout(1000);
  expect(calls).toEqual([]);
});

test("历史结果绑定了一个不存在的旧版本：带版本请求 404，不回退到不带版本的请求，不画点", async ({ page }, info) => {
  const calls = await openInjected(page, "e3-display-res-v1-0000000000000000", "e2e 历史 旧版本缺失");
  await expect(page.locator('[data-geo-version="e3-display-res-v1-0000000000000000"]')).toBeVisible();
  await expect(page.locator('[data-anchors="unavailable"]')).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(800);
  await shot(page, info, "history-missing-version");
  expect(calls.length).toBeGreaterThan(0);
  for (const u of calls) expect(new URL(u).searchParams.get("resource_version")).toBe("e3-display-res-v1-0000000000000000");
  await expect(page.locator('[data-map="paper"] [data-anchor]')).toHaveCount(0);
});
