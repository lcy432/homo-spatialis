// 手机 360×740：重新打开专属链接，从“我的运行”找到桌面主闭环跑出来的那次运行，结果三层与填写页都能读、没有横向滚动。
import { expect, test } from "@playwright/test";
import { expectAccessible, expectNoHorizontalScroll, loadIdentityA, shot, signIn } from "./helpers";

test.describe("手机 @mobile-only", () => {
  test("重新打开链接找到原来的运行；城市比较 → 单城 → 分身记录都能读", async ({ page }, info) => {
    const { token, intentId } = loadIdentityA();
    await signIn(page, token, "/runs");
    await expect(page.locator('[data-stage="finished"]')).toHaveCount(1);
    await shot(page, info, "runs");
    await expectNoHorizontalScroll(page);
    await page.getByRole("link", { name: "结果" }).click();
    await expect(page).toHaveURL(new RegExp(`/results/${intentId}$`));
    await expect(page.locator("[data-results]")).toBeVisible({ timeout: 60_000 });
    await shot(page, info, "results");
    await expectNoHorizontalScroll(page);
    await expectAccessible(page, info, "results");
    await page.getByRole("link", { name: "看这座城的详情" }).first().click();
    await expect(page.locator("[data-city-page]")).toBeVisible();
    await shot(page, info, "city");
    await expectNoHorizontalScroll(page);
    await page.getByRole("link", { name: "看这段生活" }).first().click();
    await expect(page.locator("article[data-year]").first()).toBeVisible();
    await shot(page, info, "clone");
    await expectNoHorizontalScroll(page);
  });

  test("填写页在手机上：没有横向滚动，可访问性无严重问题", async ({ page }, info) => {
    const { token } = loadIdentityA();
    await signIn(page, token, "/start");
    await page.getByRole("button", { name: "开始填写" }).click();
    await expect(page).toHaveURL(/\/place$/);
    await shot(page, info, "place");
    await expectNoHorizontalScroll(page);
    await expectAccessible(page, info, "place");
    await page.goto(page.url().replace(/\/place$/, "/priorities"));
    await shot(page, info, "priorities");
    await expectNoHorizontalScroll(page);
    await expectAccessible(page, info, "priorities");
  });
});
