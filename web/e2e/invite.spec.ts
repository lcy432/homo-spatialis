// 打开专属链接：秘密从地址栏与历史里抹掉。只有这个文件会把秘密放进地址栏，所以整个文件不留调试记录、截图与视频。
import { expect, test } from "@playwright/test";
import { createInvite } from "./helpers";

test.use({ trace: "off", screenshot: "off", video: "off" });

test("打开 /invite#token=…：换到会话，地址栏与历史里都不再有秘密", async ({ page }) => {
  const token = createInvite("e2e 链接");
  await page.goto(`/invite#token=${token}`);
  await expect(page.locator("[data-quota]")).toBeVisible({ timeout: 30_000 });
  expect(page.url()).not.toContain("token");
  expect(page.url()).not.toContain(token);
  await page.goBack().catch(() => undefined);
  expect(page.url()).not.toContain(token);
});
