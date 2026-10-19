import { expect, test } from "@playwright/test";
import { NORMAL_RUN, expectNoHorizontalScroll, fresh, shot, submissions, tabTo } from "./helpers";

test("只用键盘走完正常闭环：载入样例 → 四步 → 确认 → 预检查 → 双击运行 → 进度 → 结果 → 分身故事", async ({ page }, info) => {
  await fresh(page, "/fill/1");

  await tabTo(page, (e) => e.tag === "button" && e.text === "载入样例用户 u501 的答案");
  await page.keyboard.press("Enter");
  await expect(page.getByText(/现在显示的是合成样例用户 u501 的答案/)).toBeVisible();
  await expect(page.locator('textarea[name="occupation.raw_text"]')).toHaveValue(/全职文员/);
  await expect(page.getByText("办公室文员").first()).toBeVisible();
  await shot(page, info, "flow-1-facts");

  for (const step of [2, 3, 4]) {
    await tabTo(page, (e) => e.tag === "a" && e.text === "下一步");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(new RegExp(`/fill/${step}$`));
    await expectNoHorizontalScroll(page);
    if (step === 2) {
      await expect(page.getByRole("status").first()).toContainText("已分完");
      await shot(page, info, "flow-2-priorities");
    }
    if (step === 3) await shot(page, info, "flow-3-health");
  }

  // 第 4 步：E3 审阅（样例 03）、知悉、确认
  await expect(page.getByRole("heading", { name: "系统怎样理解（E3 审阅）" })).toBeVisible();
  await expect(page.getByText(/答案可以确认/)).toBeVisible();
  await tabTo(page, (e) => e.name === "ack.ack:standard_start");
  await page.keyboard.press("Space");
  await tabTo(page, (e) => e.tag === "button" && e.text === "确认我的信息");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "运行前说明（E3 预检查）" })).toBeVisible();
  await expect(page.getByText("每座城 1 个分身，生活 3 年。")).toBeVisible();
  await expect(page.getByText(/离线 mock，不是大语言模型/).first()).toBeVisible();
  // 内部职业代码不出现在页面上
  expect(await page.locator("body").innerText()).not.toContain("43-9061");
  await shot(page, info, "flow-4-confirm-preflight");

  // 双击“查看样例运行”：提交返回之前不触发第二次提交
  await tabTo(page, (e) => e.tag === "button" && e.text === "查看样例运行");
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/runs/${NORMAL_RUN}$`));
  const subs = await submissions(page);
  expect(subs.map((s) => s.replied)).toEqual(["07"]);

  // 进度：两个分身逐个显示
  await expect(page.getByText("正常生活完 N 年")).toHaveCount(2);
  await shot(page, info, "flow-5-progress");
  await tabTo(page, (e) => e.tag === "a" && e.text === "查看结果");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/result$/);
  await expect(page.getByText("1 个完成且可评价分身的平均 J", { exact: true })).toHaveCount(2);
  await expect(page.locator('[data-city="US:SYNTH2026:K51"]')).toContainText("0.6078");
  await expect(page.locator('[data-city="US:SYNTH2026:M52"]')).toContainText("0.6145");
  await expect(page.getByText(/不是显著性检验/).first()).toBeVisible();
  await expectNoHorizontalScroll(page);
  await shot(page, info, "flow-6-result");

  await tabTo(page, (e) => e.tag === "a" && e.text === "看这段生活");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/clones\/u501\.k51\.r0$/);
  await expect(page.getByText(/便利设备包/).first()).toBeVisible();
  await expect(page.getByText(/会计资格课程/).first()).toBeVisible();
  await expect(page.getByText(/本版结果还没有提供分身自己的理由/)).toBeVisible();
  expect(await page.locator("body").innerText()).not.toContain("43-9061");
  await expectNoHorizontalScroll(page);
  await shot(page, info, "flow-7-story");

  // 回到确认页再点一次：同一个幂等键，回执 08，同一个运行
  await page.goto("/fill/4");
  await page.getByRole("button", { name: "查看样例运行" }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${NORMAL_RUN}$`));
  const subs2 = await submissions(page);
  expect(subs2.map((s) => s.replied)).toEqual(["07", "08"]);
  expect(new Set(subs2.map((s) => s.idempotencyKey)).size).toBe(1);
});
