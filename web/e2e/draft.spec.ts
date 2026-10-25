// 草稿：四种答案状态与精确均分在服务审阅里照实出现；语言与外观切换不改答案、不额外保存；两个标签页改同一份草稿时，旧内容
// 不覆盖新内容；只用键盘也能完成主要操作。
import { expect, test } from "@playwright/test";
import { countRequests, createInvite, shot, signIn, waitSaved } from "./helpers";

async function newDraft(page: import("@playwright/test").Page, label: string): Promise<string> {
  await signIn(page, createInvite(label), "/start");
  await page.getByRole("button", { name: "开始填写" }).click();
  await expect(page).toHaveURL(/\/draft\/[^/]+\/place$/);
  return page.url().replace(/\/place$/, "");
}

test("未答、明确 0、无所谓分开保存；切换语言与外观不改答案、不发保存请求", async ({ page }) => {
  const base = await newDraft(page, "e2e 草稿状态");
  await page.goto(`${base}/priorities`);
  const six = page.locator('[data-allocator="six_dimensions"]');
  await six.locator('input[name="six_dimensions.material"]').fill("0");
  await six.locator('input[name="six_dimensions.safety"]').fill("40");
  await six.locator('input[name="six_dimensions.opportunity"]').fill("30");
  await six.locator('input[name="six_dimensions.commute"]').fill("30");
  await six.getByRole("combobox", { name: /环境/ }).selectOption("indifferent");
  await waitSaved(page);

  const puts = countRequests(page, (url, method) => method === "PUT" && url.includes("/drafts/"));
  await page.locator('[data-toggle="lang-en"]').click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Priorities");
  await page.locator('[data-toggle="mode-globe"]').click();
  await expect(page.locator("html")).toHaveAttribute("data-mode", "globe");
  await expect(six.locator('input[name="six_dimensions.material"]')).toHaveValue("0");
  await expect(six.locator('input[name="six_dimensions.safety"]')).toHaveValue("40");
  await page.locator('[data-toggle="lang-zh"]').click();
  await page.locator('[data-toggle="mode-paper"]').click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("偏好与取舍");
  await expect(six.locator('input[name="six_dimensions.material"]')).toHaveValue("0");
  expect(puts.get()).toBe(0);

  await page.goto(`${base}/review`);
  await expect(page.locator("[data-blockers]")).not.toHaveAttribute("data-blockers", "0", { timeout: 60_000 });
  const lines = page.locator('[data-compare="cmp-six_dimensions"]');
  await expect(lines.locator('[data-line="material"]')).toHaveAttribute("data-exact", "0");
  await expect(lines.locator('[data-line="environment"]')).toContainText("无所谓");
  await expect(lines.locator('[data-line="leisure"]')).toContainText("还没有回答");
});

test("两个标签页改同一份草稿：后保存的一方看到冲突，可以改用服务器的版本", async ({ context }) => {
  const p1 = await context.newPage();
  const base = await newDraft(p1, "e2e 冲突");
  await p1.goto(`${base}/more`);
  const p2 = await context.newPage();
  await p2.goto(`${base}/more`);
  await waitSaved(p2);

  await p1.locator("#free-text").fill("第一个标签页写的");
  await waitSaved(p1);
  await p2.locator("#free-text").fill("第二个标签页写的");
  await expect(p2.locator('[data-save="conflict"]')).toBeVisible({ timeout: 30_000 });
  await p2.getByRole("button", { name: "改用服务器上的版本（放弃我这里的改动）" }).click();
  await expect(p2.locator("#free-text")).toHaveValue("第一个标签页写的");
  await waitSaved(p2);
});

test("只用键盘：勾选城市、主动均分（对话框 Enter 确认）、Esc 关闭对话框后焦点回到按钮", async ({ page }) => {
  const base = await newDraft(page, "e2e 键盘");
  await page.getByRole("button", { name: "清除选择" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status").filter({ hasText: "已选" })).toHaveText("已选：0 座");
  const first = page.locator(".city-list input[type=checkbox]").first();
  await first.focus();
  await page.keyboard.press("Space");
  await expect(first).toBeChecked();

  await page.goto(`${base}/priorities`);
  const split = page.locator('[data-allocator="six_dimensions"]').getByRole("button", { name: "平均分配" });
  await split.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(split).toBeFocused();
  await page.keyboard.press("Enter");
  await page.getByRole("dialog").getByRole("button", { name: "平均分配" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator('[data-allocator="six_dimensions"]').getByText(/每项 16.7 分（精确值 50\/3）/)).toBeVisible();
});

test("地图上的城市点来自服务的地理清单：真实六城都有点；点一个点就是切换这座城，与列表同步", async ({ page }, info) => {
  await signIn(page, createInvite("e2e 城市点"), "/start");
  await page.getByRole("button", { name: "开始填写" }).click();
  await expect(page).toHaveURL(/\/place$/);
  await expect(page.locator('[data-anchor-count="6"]')).toBeVisible();
  await expect(page.locator('[data-map="paper"] [data-anchor]')).toHaveCount(6);
  await expect(page.getByText(/城市点来自服务的地理显示清单/)).toBeVisible();
  const count = page.getByRole("status").filter({ hasText: "已选" });
  await expect(count).toHaveText("已选：6 座");
  // 点西雅图的点：取消选择（列表里的勾也跟着掉）
  await page.locator('[data-map="paper"] [data-anchor="US:CBSA2023:42660"] circle').click();
  await expect(count).toHaveText("已选：5 座");
  await expect(page.locator('[data-city-ref="US:CBSA2023:42660"] input')).not.toBeChecked();
  // 再点一次：选回来
  await page.locator('[data-map="paper"] [data-anchor="US:CBSA2023:42660"] circle').click();
  await expect(count).toHaveText("已选：6 座");
  await waitSaved(page);
  await shot(page, info, "place-anchors");
});
