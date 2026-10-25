// 会话与身份：换身份不闪现另一个人的内容；别人的编号打不开；退出之后私有页面锁住；删除全部内容之后旧页面不再显示缓存里的内容。
// （打开专属链接见 invite.spec.ts）
import { expect, test } from "@playwright/test";
import { createInvite, signIn } from "./helpers";

test("换邀请身份：看不到上一个人的草稿；上一个人的草稿编号打不开", async ({ page }) => {
  const b = createInvite("e2e 身份 B");
  const c = createInvite("e2e 身份 C");
  await signIn(page, b, "/start");
  await page.getByRole("button", { name: "开始填写" }).click();
  await expect(page).toHaveURL(/\/draft\/[^/]+\/place$/);
  const draftUrl = page.url();
  await signIn(page, c, "/");
  await expect(page.getByText("还没有草稿。")).toBeVisible();
  await page.goto(draftUrl);
  await expect(page.locator('[data-error="not_found"]')).toBeVisible({ timeout: 30_000 });
});

test("删除全部内容：服务受理（写明是“删除中”，不说已彻底清除）；后退回草稿页显示找不到，首页没有草稿", async ({ page }) => {
  await signIn(page, createInvite("e2e 删除"), "/start");
  await page.getByRole("button", { name: "开始填写" }).click();
  await expect(page).toHaveURL(/\/draft\/[^/]+\/place$/);
  const draftUrl = page.url();
  await expect(page.locator("[data-city-ref]").first()).toBeVisible();
  // 站内导航（不重新加载页面）：草稿还在查询缓存里
  await page.getByRole("link", { name: "设置与删除" }).click();
  await page.getByRole("button", { name: "删除全部内容" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "删除全部内容" }).click();
  await expect(page.locator("[data-deletion]")).toHaveAttribute("data-deletion", "deleting", { timeout: 30_000 });
  await expect(page.getByText("删除已受理：")).toBeVisible();
  // 浏览器后退回到草稿页：不显示缓存里的旧草稿，服务说找不到
  await page.goBack();
  await expect(page).toHaveURL(draftUrl);
  await expect(page.locator('[data-error="not_found"]')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("[data-city-ref]")).toHaveCount(0);
  await page.getByRole("link", { name: "首页" }).click();
  await expect(page.getByText("还没有草稿。")).toBeVisible();
});

test("退出这个设备：私有页面锁住，需要重新打开专属链接", async ({ page }) => {
  const d = createInvite("e2e 退出");
  await signIn(page, d, "/settings");
  await page.getByRole("button", { name: "退出这个设备" }).click();
  await expect(page.locator('[data-session="signed_out"]')).toBeVisible();
  await page.goto("/runs");
  await expect(page.locator('[data-session="none"]')).toBeVisible();
});
