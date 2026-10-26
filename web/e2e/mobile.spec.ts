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
    // 等底图画出来再截（截图是给人看的；页面本身不等它）
    await expect(page.locator('[data-map="paper"] [data-country]').first()).toBeVisible({ timeout: 30_000 });
    await shot(page, info, "place");
    await expectNoHorizontalScroll(page);
    await expectAccessible(page, info, "place");
    // 第 2 步：目录在手机上用下拉选分类 + 搜索；选中后目录收起
    await page.goto(page.url().replace(/\/place$/, "/facts"));
    await page.getByRole("combobox", { name: "分类" }).selectOption("office_service");
    await expect(page.locator("[data-occupation]").first()).toBeVisible();
    await shot(page, info, "facts-catalog");
    await expectNoHorizontalScroll(page);
    await expectAccessible(page, info, "facts");
    await page.locator("[data-occupation-search]").fill("文员");
    await page.locator('[data-occupation="occ.office_clerk"] button').click();
    await expect(page.locator('[data-selected-occupation="occ.office_clerk"]')).toBeVisible();
    await shot(page, info, "facts");
    await expectNoHorizontalScroll(page);
    // 第 3 步：饼图与滑块（触屏尺寸）
    await page.goto(page.url().replace(/\/facts$/, "/priorities"));
    await expect(page.locator('[data-allocator="six_dimensions"] [data-slider="safety"]')).toBeVisible();
    // 触屏尺寸下“记为 0”按钮可点：明确的 0
    await page.locator('[data-allocator="six_dimensions"] [data-key="leisure"] [data-action="zero"]').click();
    await expect(page.locator('[data-allocator="six_dimensions"] [data-key="leisure"] [data-value]')).toHaveAttribute("data-value", "0");
    await shot(page, info, "priorities");
    await expectNoHorizontalScroll(page);
    await expectAccessible(page, info, "priorities");
    // 第 4 步：面板
    await page.goto(page.url().replace(/\/priorities$/, "/more"));
    await expect(page.locator('[data-panel="taste"] [data-panel-search]')).toBeVisible();
    await shot(page, info, "more");
    await expectNoHorizontalScroll(page);
    await expectAccessible(page, info, "more");
  });
});
