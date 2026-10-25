// 写实地球：实际鼠标拖动改变视角，松手不误改城市选择；“回到全球视角”；地球与列表、平面地图共用同一份选择。
// 判断“视角变了”读地球的视角数值（开发构建暴露的 window.__E5_GLOBE__），不比较像素。
import { expect, test } from "@playwright/test";
import { createInvite, shot, signIn } from "./helpers";

test("地球模式：拖动转动地球、松手不误选；重置视角；切回稿纸后选择不变", async ({ page }, info) => {
  await signIn(page, createInvite("e2e 地球"), "/start");
  await page.getByRole("button", { name: "开始填写" }).click();
  await expect(page).toHaveURL(/\/place$/);
  const count = page.getByRole("status").filter({ hasText: "已选" });
  const before = await count.textContent();

  await page.locator('[data-toggle="mode-globe"]').click();
  await expect(page.locator('[data-map="globe"] canvas')).toBeVisible({ timeout: 60_000 });
  await page.waitForFunction(() => !!window.__E5_GLOBE__);
  const pov0 = await page.evaluate(() => window.__E5_GLOBE__?.pov());
  const box = await page.locator('[data-map="globe"] canvas').boundingBox();
  if (!box) throw new Error("地球没有画出来");
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  await page.mouse.move(cx - 150, cy);
  await page.mouse.down();
  for (let i = 1; i <= 15; i += 1) await page.mouse.move(cx - 150 + i * 20, cy + i * 2);
  await page.mouse.up();
  await page.waitForTimeout(400);
  const pov1 = await page.evaluate(() => window.__E5_GLOBE__?.pov());
  expect(Math.abs((pov1?.lng ?? 0) - (pov0?.lng ?? 0))).toBeGreaterThan(5);
  // 拖动结束不算点击：城市选择没变
  await expect(count).toHaveText(before ?? "");
  await shot(page, info, "globe-place");

  await page.getByRole("button", { name: "回到全球视角" }).click();
  await page.waitForTimeout(1200);
  const pov2 = await page.evaluate(() => window.__E5_GLOBE__?.pov());
  // 总览对准现在能选的国家（本资料包只有美国：美国大陆部分的质心附近）
  expect(pov2?.altitude).toBeCloseTo(1.9, 1);
  expect(pov2?.lng ?? 0).toBeGreaterThan(-110);
  expect(pov2?.lng ?? 0).toBeLessThan(-85);
  expect(pov2?.lat ?? 0).toBeGreaterThan(30);
  expect(pov2?.lat ?? 0).toBeLessThan(48);

  await page.locator('[data-toggle="mode-paper"]').click();
  await expect(page.locator('[data-map="paper"]')).toBeVisible();
  await expect(count).toHaveText(before ?? "");
});
