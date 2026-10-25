// 验收截图：用主闭环跑出来的那次运行，在“中文 / 英文 × 稿纸 / 地球”四种组合下逐页截图（写进本机 runs/web-e2e/screenshots/），
// 供人工检查长英文、纸纹对比度、非颜色提示、标签遮挡与手机表格。切换语言与外观不改任何答案或结果。
import { expect, test } from "@playwright/test";
import { expectNoHorizontalScroll, loadIdentityA, shot, signIn } from "./helpers";

const COMBOS = [
  { lang: "zh", mode: "paper" },
  { lang: "en", mode: "paper" },
  { lang: "zh", mode: "globe" },
  { lang: "en", mode: "globe" },
] as const;

test("四种语言 × 外观组合的关键页面截图 @mobile", async ({ page }, info) => {
  test.setTimeout(6 * 60_000);
  const { token, intentId } = loadIdentityA();
  await signIn(page, token, "/");
  for (const { lang, mode } of COMBOS) {
    await page.locator(`[data-toggle="lang-${lang}"]`).click();
    await page.locator(`[data-toggle="mode-${mode}"]`).click();
    await expect(page.locator("html")).toHaveAttribute("data-mode", mode);
    const tag = `${lang}-${mode}`;
    await page.goto("/");
    await shot(page, info, `${tag}-home`);
    await page.goto("/start");
    await shot(page, info, `${tag}-start`);
    await page.goto(`/intents/${encodeURIComponent(intentId)}`);
    await expect(page.locator("[data-stage]")).toBeVisible();
    await shot(page, info, `${tag}-intent`);
    await page.goto(`/results/${encodeURIComponent(intentId)}`);
    await expect(page.locator("[data-results]")).toBeVisible({ timeout: 60_000 });
    // 截整页之前等地图画完（底图是另取的文件；没等到就截，会截到布局正在变化的一刻）
    await expect(page.locator("[data-geo-version]")).toHaveAttribute("data-geo-version", /^e3-display-res-/, { timeout: 30_000 });
    if (mode === "paper") await expect(page.locator('[data-map="paper"] [data-country]').first()).toBeVisible({ timeout: 30_000 });
    if (mode === "globe") {
      await expect(page.locator('[data-map="globe"] canvas')).toBeVisible({ timeout: 60_000 });
      await page.waitForTimeout(1500);
    }
    await shot(page, info, `${tag}-results`);
    await expectNoHorizontalScroll(page);
    await page.locator("article[data-city] a").first().click();
    await expect(page.locator("[data-city-page]")).toBeVisible();
    await shot(page, info, `${tag}-city`);
    await page.locator("[data-clone] a").first().click();
    await expect(page.locator("[data-clone-page]")).toBeVisible();
    await shot(page, info, `${tag}-clone`);
    await expectNoHorizontalScroll(page);
  }
});
