// 手机项目的最后一条（文件名排在最后）：桌面主闭环跑出来的那次运行先给前面的手机故事与截图用，这里再删掉它。
// 等待页的完成状态与授权状态；删除这次运行之后结果、显示信封都不可读，缓存不复活，“我的运行”里不再有已结束的运行。
import { expect, test } from "@playwright/test";
import { expectAccessible, expectNoHorizontalScroll, loadIdentityA, shot, signIn } from "./helpers";

test.describe("手机 @mobile-only", () => {
  test("等待页与删除这次运行：进度条、授权状态；删除后结果与显示信封不可读、缓存不复活、列表里不再有已结束的运行", async ({ page }, info) => {
    test.setTimeout(4 * 60_000);
    const { token, intentId } = loadIdentityA();
    await signIn(page, token, `/intents/${encodeURIComponent(intentId)}`);
    await expect(page.locator("[data-phase]")).toHaveAttribute("data-phase", "finished", { timeout: 60_000 });
    await expect(page.locator('[data-authorization="used"]')).toBeVisible();
    await shot(page, info, "waiting-finished");
    await expectNoHorizontalScroll(page);
    await expectAccessible(page, info, "waiting");
    // 先把结果读进缓存，再删
    await page.getByRole("link", { name: "查看结果" }).click();
    await expect(page.locator("[data-results]")).toBeVisible({ timeout: 60_000 });
    await page.goBack();
    await expect(page.locator("[data-phase]")).toBeVisible();
    await page.locator('[data-action="delete"]').click();
    await page.locator('[data-action="confirm-action"]').click();
    await expect(page.locator("[data-deletion]")).toHaveAttribute("data-deletion", /deleting|deleted/, { timeout: 30_000 });
    await expect(page.locator('[data-action="delete"]')).toHaveCount(0);
    await shot(page, info, "run-deleted");
    // 结果页：服务说已删除（或删除中），没有缓存里的旧结果
    await page.goto(`/results/${encodeURIComponent(intentId)}`);
    await expect(page.locator('[data-error="deleted"], [data-error="deleting"], [data-error="not_found"], [data-results-unavailable]').first()).toBeVisible({ timeout: 30_000 });
    await expect(page.locator("[data-results]")).toHaveCount(0);
    const display = await page.request.get(`/api/v1/display?kind=result&id=${encodeURIComponent(intentId)}&locale=zh`);
    expect([404, 409, 410]).toContain(display.status());
    await page.goto("/runs");
    await expect(page.locator('[data-stage="finished"]')).toHaveCount(0);
  });
});
