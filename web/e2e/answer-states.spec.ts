import { readFileSync } from "node:fs";
import { type Page, expect, test } from "@playwright/test";
import { evidencePath, fresh, shot } from "./helpers";

const six = (page: Page, key: string) => page.locator(`input[name="six_dimensions.${key}"]`);
const status = (page: Page) => page.getByRole("status").first();

async function exportDraft(page: Page, project: string, name: string): Promise<Record<string, any>> {
  await page.goto("/fill/4");
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "导出草稿（JSON）" }).click(),
  ]);
  const file = evidencePath("drafts", project, `${name}.json`);
  await download.saveAs(file);
  return JSON.parse(readFileSync(file, "utf8"));
}

test("未答、明确 0、无所谓、暂不确定分开保存，刷新之后不变", async ({ page }, info) => {
  await fresh(page, "/fill/2");
  await six(page, "material").fill("0");
  await six(page, "safety").fill("40");
  await six(page, "opportunity").fill("30");
  await six(page, "commute").fill("30");
  await page.getByLabel("环境：说不准？").selectOption("indifferent");
  // 休闲不填
  await expect(status(page)).toContainText("已分完");
  await expect(page.getByText(/不适用（物质生活为 0 分）/)).toBeVisible();
  await shot(page, info, "states-priorities");

  await page.goto("/fill/3");
  await page.getByRole("radio", { name: "暂不确定" }).first().check();
  await page.getByRole("radio", { name: "先跳过" }).nth(1).check();

  await page.reload();
  await page.goto("/fill/2");
  await expect(six(page, "material")).toHaveValue("0");
  await expect(six(page, "leisure")).toHaveValue("");
  await expect(page.getByLabel("环境：说不准？")).toHaveValue("indifferent");
  await expect(page.locator('[data-key="leisure"]')).toContainText("还没有分配");

  const draft = await exportDraft(page, info.project.name, "states");
  expect(draft.six_dimensions.entries.material).toEqual({ state: "provided", value_text: "0" });
  expect(draft.six_dimensions.entries.environment).toEqual({ state: "indifferent" });
  expect(draft.six_dimensions.entries.leisure).toBeUndefined();
  expect(draft.health.items).toEqual(
    expect.arrayContaining([
      { question_id: "hc_more_pay_more_sick_risk", choice: "unsure" },
      { question_id: "hc_reserve_for_health_costs", choice: null },
    ]),
  );
  await expect(page.getByText(/这是你填写内容的回显，不是系统的理解/)).toBeVisible();
  await shot(page, info, "states-echo");
});

test("均分之后改一项：精确多出 10/3（显示 3.3），由用户点选从哪一项减", async ({ page }, info) => {
  await fresh(page, "/fill/2");
  await page.getByRole("button", { name: "平均分配" }).first().click();
  await page.getByRole("dialog").getByRole("button", { name: "平均分配" }).click();
  await expect(six(page, "material")).toHaveValue("16.7");
  await expect(status(page)).toContainText("已分完");

  await six(page, "safety").fill("20");
  await expect(page.getByRole("dialog")).toContainText("其余各项保留精确的 50/3");
  await page.getByRole("dialog").getByRole("button", { name: "改成手动分配" }).click();
  await expect(status(page)).toContainText("多出 3.3 分");
  await expect(status(page)).toContainText("10/3");
  await shot(page, info, "equal-split-over");

  const over = await exportDraft(page, info.project.name, "equal-split-over");
  expect(over.six_dimensions.mode).toBe("manual");
  expect(over.six_dimensions.entries.safety).toEqual({ state: "provided", value_text: "20" });
  for (const k of ["material", "opportunity", "leisure", "environment", "commute"]) {
    expect(over.six_dimensions.entries[k]).toEqual({ state: "provided", value_text: "50/3" });
  }

  await page.goto("/fill/2");
  await page.getByRole("button", { name: /从哪一项减/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: /物质生活/ }).click();
  await expect(status(page)).toContainText("已分完");
  await expect(six(page, "material")).toHaveValue("40/3");
  await shot(page, info, "equal-split-fixed");
  const fixed = await exportDraft(page, info.project.name, "equal-split-fixed");
  expect(fixed.six_dimensions.entries.material).toEqual({ state: "provided", value_text: "40/3" });
});

test("金额写法原样保留，只提示规则，不替用户“修正”", async ({ page }, info) => {
  await fresh(page, "/fill/1");
  const cash = page.locator('input[name="cash.amount_text"]');
  await cash.fill("3000,50");
  await expect(page.getByText(/这个写法看起来不符合上面的规则/)).toBeVisible();
  await page.reload();
  await expect(cash).toHaveValue("3000,50");
  await shot(page, info, "money-comma");
  const draft = await exportDraft(page, info.project.name, "money-comma");
  expect(draft.cash).toMatchObject({ state: "provided", amount_text: "3000,50", currency: "USD", price_period: "2024" });
});

test("撤销以一次操作为一步", async ({ page }) => {
  await fresh(page, "/fill/2");
  await six(page, "material").fill("30");
  await six(page, "safety").fill("20");
  await page.getByRole("button", { name: "撤销" }).first().click();
  await expect(six(page, "safety")).toHaveValue("");
  await expect(six(page, "material")).toHaveValue("30");
  await page.getByRole("button", { name: "重做" }).first().click();
  await expect(six(page, "safety")).toHaveValue("20");
});
