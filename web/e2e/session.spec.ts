// 会话与身份：换身份不闪现另一个人的内容；别人的编号打不开；退出之后私有页面锁住；删除全部内容之后旧页面不再显示缓存里的内容。
// （打开专属链接见 invite.spec.ts）
import { expect, test } from "@playwright/test";
import { createInvite, signIn, waitSaved } from "./helpers";

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

test("退出遇到断网：没有确认就不说已退出；网络恢复后重新检查回到空间；回执丢了再试一次才算退出", async ({ page }) => {
  await signIn(page, createInvite("e2e 退出断网"), "/settings");
  const isSignOut = (r: { method(): string; url(): string }) => r.method() === "DELETE" && /\/api\/v1\/session$/.test(r.url());
  // 1) 请求根本没送到服务
  await page.route("**/api/v1/session", (route) => (isSignOut(route.request()) ? route.abort("connectionfailed") : route.continue()));
  await page.getByRole("button", { name: "退出这个设备" }).click();
  await expect(page.locator('[data-session="sign_out_unconfirmed"]')).toBeVisible();
  await expect(page.getByText(/会话可能仍然有效/)).toBeVisible();
  await expect(page.locator('[data-session="signed_out"]')).toHaveCount(0);
  await page.unroute("**/api/v1/session");
  // 网络恢复：重新检查——服务那边会话还在，回到自己的空间（没有身份串用：还是同一个人的设置页）
  await page.getByRole("button", { name: "重新检查" }).click();
  await expect(page.getByRole("button", { name: "退出这个设备" })).toBeVisible();
  // 2) 服务处理了，但回执在路上丢了
  await page.route("**/api/v1/session", async (route) => {
    if (!isSignOut(route.request())) return route.continue();
    await route.fetch();
    await route.abort("connectionfailed");
  });
  await page.getByRole("button", { name: "退出这个设备" }).click();
  await expect(page.locator('[data-session="sign_out_unconfirmed"]')).toBeVisible();
  await page.unroute("**/api/v1/session");
  // 再试一次退出：服务回答“没有这个会话”，这才算退出成功
  await page.getByRole("button", { name: "再试一次退出" }).click();
  await expect(page.locator('[data-session="signed_out"]')).toBeVisible();
  await page.reload();
  await expect(page.locator('[data-session="none"]')).toBeVisible();
});

test("新建草稿后站内回首页：立刻有“继续填写”，打开的是同一份草稿、答案还在；没有多建草稿", async ({ page }) => {
  await signIn(page, createInvite("e2e 回首页"), "/start");
  await page.getByRole("button", { name: "开始填写" }).click();
  await expect(page).toHaveURL(/\/draft\/[^/]+\/place$/);
  const draftUrl = page.url();
  await page.getByRole("button", { name: "清除选择" }).click();
  await page.locator('[data-city-ref="US:CBSA2023:42660"] input').check();
  await waitSaved(page);
  await page.getByRole("link", { name: "首页" }).click();
  await expect(page.getByRole("link", { name: "继续填写" })).toHaveCount(1);
  await expect(page.getByText("还没有草稿。")).toHaveCount(0);
  await page.getByRole("link", { name: "继续填写" }).click();
  await expect(page).toHaveURL(draftUrl);
  await expect(page.getByRole("status").filter({ hasText: "已选" })).toHaveText("已选：1 座");
});
