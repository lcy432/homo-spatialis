// 一次“确认并开始”的找回与取消（真实本地服务）：
// * 回复丢了（请求被中断）：页面保留同一个键的本地记录，“找回”重发同一个请求 → 服务给回同一个意图；全程只有一个意图、没有 submit。
// * 刷新之后首页也能找回；开始之前可以取消（取消后次数不变）。
import { expect, test } from "@playwright/test";
import { ORIGIN, countRequests, createInvite, shot, signIn, waitSaved } from "./helpers";

async function confirmedProfile(page: import("@playwright/test").Page, label: string): Promise<void> {
  await signIn(page, createInvite(label), "/start");
  await page.getByRole("button", { name: "开始填写" }).click();
  await expect(page).toHaveURL(/\/draft\/[^/]+\/place$/);
  const base = page.url().replace(/\/place$/, "");
  await page.goto(`${base}/facts`);
  await page.locator("[data-occupation-search]").fill("文员");
  await page.locator('[data-occupation="occ.office_clerk"] button').click();
  await page.locator("#age").fill("35");
  await page.locator("#cash").fill("3000");
  await page.locator('input[name="education"][value="high_school"]').check();
  await page.locator('[data-certificates-state="none"]').click();
  await waitSaved(page);
  await page.goto(`${base}/priorities`);
  await page.locator('[data-allocator="six_dimensions"] [data-action="equal-weight"]').click();
  await page.locator('[data-allocator="material"] [data-action="equal-weight"]').click();
  await waitSaved(page);
  await page.goto(`${base}/review`);
  await expect(page.locator("[data-blockers]")).toHaveAttribute("data-blockers", "0", { timeout: 60_000 });
  for (const ack of await page.locator("[data-ack] input[type=checkbox]").all()) await ack.check();
  await page.getByRole("button", { name: "确认这些理解" }).click();
  await expect(page.locator("#start")).toBeVisible({ timeout: 30_000 });
}

test("回复丢了：同一个键找回同一个意图；没有第二个意图、没有 submit；开始之前可以取消", async ({ page }, info) => {
  test.setTimeout(5 * 60_000);
  const starts = countRequests(page, (url, method) => method === "POST" && /\/run-intents$/.test(new URL(url).pathname));
  const submits = countRequests(page, (url, method) => method === "POST" && url.includes("/submit"));
  await confirmedProfile(page, "e2e 找回");
  await page.locator("#opt-clones").fill("1");
  await expect(page.locator("[data-start-preview]")).toBeVisible({ timeout: 30_000 });
  for (const a of await page.locator("[data-start-ack] input[type=checkbox]").all()) await a.check();
  // 第一次发送：请求送出去了，但回复到不了浏览器（服务端可能已经建了意图）
  let aborted = 0;
  await page.route("**/api/v1/run-intents", async (route) => {
    if (route.request().method() === "POST" && aborted === 0) {
      aborted += 1;
      // 让请求真的到服务端再掐断回复：先转发、丢掉结果
      await route.fetch().catch(() => undefined);
      return route.abort("failed");
    }
    return route.continue();
  });
  await page.locator('[data-action="start"]').click();
  const pending = page.locator("[data-pending-start]");
  await expect(pending).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('[data-action="start"]')).toHaveCount(0);
  await shot(page, info, "start-lost-reply");
  // 刷新：记录还在（本地），找回按钮照旧
  await page.reload();
  await expect(page.locator("[data-pending-start-notice]")).toBeVisible({ timeout: 30_000 });
  await page.locator('[data-pending-start-notice] [data-action="recover-start"]').click();
  await expect(page).toHaveURL(/\/intents\/[^/]+$/, { timeout: 30_000 });
  expect(starts.get()).toBe(2);
  expect(submits.get()).toBe(0);
  const runs = await (await page.request.get(`${ORIGIN}/api/v1/runs`)).json();
  expect(runs.runs).toHaveLength(1);
  const intentId = decodeURIComponent(page.url().split("/intents/")[1] ?? "");
  expect(runs.runs[0].intent_id).toBe(intentId);
  await expect(page.locator("[data-start-state]")).toHaveAttribute("data-start-state", /pending|used/);
  // 找回之后本地记录清掉：首页不再提示
  await page.goto("/");
  await expect(page.locator("[data-pending-start-notice]")).toHaveCount(0);
  await page.goto(`/intents/${encodeURIComponent(intentId)}`);
  // 开始之前取消（允许的动作）：取消后阶段 cancelled、授权作废、次数不变
  const cancel = page.locator('[data-action="cancel"]');
  if (await cancel.count()) {
    await cancel.click();
    await page.locator('[data-action="confirm-action"]').click();
    await expect(page.locator("[data-phase]")).toHaveAttribute("data-phase", "cancelled", { timeout: 30_000 });
    await expect(page.locator("[data-start-state]")).toHaveAttribute("data-start-state", "void");
    await expect(page.locator('[data-void-reason="cancelled"]')).toBeVisible();
    await shot(page, info, "start-cancelled");
    await page.goto("/");
    await expect(page.locator("[data-quota]")).toHaveAttribute("data-quota", "3/0/0/3");
  }
  expect(submits.get()).toBe(0);
});
