import { expect, test } from "@playwright/test";
import { expectNoHorizontalScroll, fresh, shot } from "./helpers";

test("每一页都有合成样例横幅，四步都能打开、没有横向滚动", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  await fresh(page);
  for (const step of [1, 2, 3, 4]) {
    await page.goto(`/fill/${step}`);
    await expect(page.getByRole("note").filter({ hasText: "合成样例" })).toBeVisible();
    await expect(page.locator("#page-title")).toContainText(`${step} ·`);
    await expectNoHorizontalScroll(page);
    await shot(page, info, `blank-step-${step}`);
  }
  expect(errors).toEqual([]);
});
