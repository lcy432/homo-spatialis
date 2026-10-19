import { type Page, expect, test } from "@playwright/test";
import { fresh, setTransport, shot, submissions } from "./helpers";

async function confirmSample(page: Page) {
  await fresh(page, "/fill/1");
  await page.getByRole("button", { name: "载入样例用户 u501 的答案" }).click();
  await page.goto("/fill/4");
  await page.locator('input[name="ack.ack:standard_start"]').check();
  await page.getByRole("button", { name: "确认我的信息" }).click();
  await expect(page.getByRole("button", { name: "查看样例运行" })).toBeVisible();
}

test("断网时提交没有送达；回执丢失后用同一个幂等键重试，拿到同一个运行；刷新进度页不重新提交", async ({ page }, info) => {
  await confirmSample(page);

  await page.context().setOffline(true);
  await page.getByRole("button", { name: "查看样例运行" }).click();
  await expect(page.getByText(/网络断了，提交没有完成/)).toBeVisible();
  expect(await submissions(page)).toHaveLength(0);
  await shot(page, info, "submit-offline");
  await page.context().setOffline(false);

  await setTransport(page, { dropNextReceipt: true });
  await page.getByRole("button", { name: "查看样例运行" }).click();
  await expect(page.getByText(/网络断了，提交没有完成/)).toBeVisible();
  let subs = await submissions(page);
  expect(subs.map((s) => s.replied)).toEqual(["lost"]);
  const key = subs[0]!.idempotencyKey;

  await page.getByRole("button", { name: "查看样例运行" }).click();
  await expect(page).toHaveURL(/\/runs\/run-e3a-33299bbc42cf707b$/);
  subs = await submissions(page);
  expect(subs.map((s) => s.replied)).toEqual(["lost", "08"]);
  expect(subs.every((s) => s.idempotencyKey === key)).toBe(true);
  const stored = await page.evaluate(() => JSON.parse(sessionStorage.getItem("e5a.flow.v1") ?? "{}"));
  expect(Object.values(stored.idempotencyKeys)).toEqual([key]);

  // 刷新进度页：页面重新加载，只查询，不产生新的提交（“服务端”的提交登记还是那两条）
  await page.reload();
  await expect(page.getByText("正常生活完 N 年")).toHaveCount(2);
  expect((await submissions(page)).map((s) => s.replied)).toEqual(["lost", "08"]);
});

test("确认之后改了答案：确认与预检查失效，运行按钮不再出现", async ({ page }, info) => {
  await confirmSample(page);
  await page.goto("/fill/2");
  await page.locator('input[name="six_dimensions.leisure"]').fill("15");
  await page.goto("/fill/4");
  await expect(page.getByText("已失效（你在确认后改了答案），需要重新确认。")).toBeVisible();
  await expect(page.getByRole("button", { name: "查看样例运行" })).toHaveCount(0);
  await expect(page.getByText(/这是你填写内容的回显/)).toBeVisible();
  await shot(page, info, "edited-after-confirm");
});

test("服务端拒绝过期的提交（样例 31）：显示原因，引导重新确认", async ({ page }, info) => {
  await fresh(page, "/dev/samples");
  await page.locator('[data-scenario="stale_submission"]').click();
  await expect(page).toHaveURL(/\/fill\/1$/);
  await page.goto("/fill/4");
  await page.locator('input[name="ack.ack:standard_start"]').check();
  await page.getByRole("button", { name: "确认我的信息" }).click();
  await page.getByRole("button", { name: "查看样例运行" }).click();
  await expect(page.getByText("服务端拒绝了这次提交：")).toBeVisible();
  await expect(page.getByText("提交引用的确认画像不是当前这一份")).toBeVisible();
  await shot(page, info, "submit-rejected-31");
});
