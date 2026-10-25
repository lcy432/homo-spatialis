// 没有 WebGL 的设备：地球模式说明原因、退回平面地图，城市列表照常可用，不阻断填写。
import { expect, test } from "@playwright/test";
import { createInvite, signIn } from "./helpers";

test.use({ launchOptions: { args: ["--disable-webgl", "--disable-webgl2", "--disable-3d-apis"] } });

test("没有 WebGL：地球模式退回平面地图并说明，列表照常可用", async ({ page }) => {
  await signIn(page, createInvite("e2e 无 WebGL"), "/start");
  await page.getByRole("button", { name: "开始填写" }).click();
  await expect(page).toHaveURL(/\/place$/);
  await page.locator('[data-toggle="mode-globe"]').click();
  await expect(page.locator('[data-globe="unavailable"]')).toBeVisible();
  await expect(page.locator('[data-map="paper"]')).toBeVisible();
  await page.getByRole("button", { name: "清除选择" }).click();
  await expect(page.getByRole("status").filter({ hasText: "已选" })).toHaveText("已选：0 座");
});
