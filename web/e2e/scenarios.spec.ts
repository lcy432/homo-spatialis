import { type Page, expect, test } from "@playwright/test";
import { expectNoHorizontalScroll, fresh, sampleRunId, shot } from "./helpers";

async function openScenario(page: Page, id: string) {
  await fresh(page, "/dev/samples");
  await page.locator(`[data-scenario="${id}"]`).click();
}

test("C / E / M 混合（样例 23）：C = 0 不显示 0，缺失范围分母 1，模拟问题不写成出局", async ({ page }, info) => {
  await openScenario(page, "mixed_cem");
  const m52 = page.locator('[data-city="US:SYNTH2026:M52"]');
  await expect(m52).toContainText("没有可用于平均的完整结果");
  await expect(m52).toContainText("分母 1 = 完成 + 模拟问题，出局者不进");
  await expect(m52).toContainText("原计划 2 个分身：0 个完成且可评价，1 个生活出局，1 个模拟问题");
  await expect(page.locator('[data-city="US:SYNTH2026:K51"]')).toContainText("0.6078");
  await expect(page.locator('[data-clone="u503.m52.r0"]')).toContainText("不写成生活出局");
  // 城市卡按 E3 的顺序，不排序
  const order = await page.locator("[data-city]").evaluateAll((els) => els.map((e) => e.getAttribute("data-city")));
  expect(order).toEqual(["US:SYNTH2026:K51", "US:SYNTH2026:M52"]);
  await expectNoHorizontalScroll(page);
  await shot(page, info, "result-23-mixed");
  await page.locator('[data-clone="u503.k51.r1"]').getByRole("link", { name: "看这段生活" }).click();
  await expect(page.getByText(/付不起最低生活，出局/)).toBeVisible();
  await shot(page, info, "story-23-exit");
});

test("全部出局（样例 24）：均值与范围都为空", async ({ page }, info) => {
  await openScenario(page, "all_exit");
  for (const ref of ["US:SYNTH2026:K51", "US:SYNTH2026:M52"]) {
    const c = page.locator(`[data-city="${ref}"]`);
    await expect(c).toContainText("没有可用于平均的完整结果");
    await expect(c).toContainText("没有范围");
  }
  await shot(page, info, "result-24-all-exit");
});

test("中断（样例 26 → 25）：可以恢复但没有恢复按钮；评价还没生成就不显示任何分类", async ({ page }, info) => {
  await openScenario(page, "interrupted");
  await expect(page.getByText("在两次规划调用之间中断").first()).toBeVisible();
  await expect(page.getByText(/由维护者处理/)).toBeVisible();
  await expect(page.getByRole("button", { name: /恢复/ })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "查看结果" })).toHaveCount(0);
  await shot(page, info, "progress-26-interrupted");
  await page.goto(`/runs/${sampleRunId("25")}/result`);
  await expect(page.getByText("这次运行还没有生成评价：不显示任何城市分类、均值或范围。")).toBeVisible();
  await expect(page.locator("[data-city]")).toHaveCount(0);
  await shot(page, info, "result-25-not-generated");
});

test("程序错误暂停（样例 27 → 27b）：模拟问题，不写成“这座城生活不下去”", async ({ page }, info) => {
  await openScenario(page, "paused");
  await expect(page.getByText(/批次因程序或配置问题暂停/)).toBeVisible();
  await shot(page, info, "progress-27-paused");
  await page.getByRole("link", { name: "查看结果" }).click();
  await expect(page.getByText(/模拟程序或配置出错，这个分身没有完成（不是生活出局）/)).toBeVisible();
  expect(await page.locator("body").innerText()).not.toMatch(/生活不下去/);
  await shot(page, info, "result-27b-paused");
});

test("服务失败停下（契约示例 33）：不是生活结果", async ({ page }, info) => {
  await openScenario(page, "halted");
  await expect(page.getByText(/模型服务暂时不可用/)).toBeVisible();
  await expect(page.getByText(/不是生活结果/)).toBeVisible();
  await expect(page.getByText(/标明为示例的契约响应/)).toBeVisible();
  await shot(page, info, "progress-33-halted");
});

test("正权重资料未知（样例 34）：说明“该维度在本次比较中无数据支持”，可评分是部分", async ({ page }, info) => {
  await openScenario(page, "weight_unknown");
  await expect(page.getByText(/该维度在本次比较中无数据支持/)).toBeVisible();
  await expect(page.getByText(/合成城 M52 缺少“安全”评分要读的资料/).first()).toBeVisible();
  await shot(page, info, "preflight-34-unknown");
});

test("审阅样例：职业歧义（11）、旧洗衣（16）、上门顾虑（17）、金额写法（37）、重复答案（38）", async ({ page }, info) => {
  for (const [id, text] of [
    ["occupation_ambiguous", "柜台点单与饮品制作"],
    ["legacy_laundry", "旧问卷的“洗衣”问的是有没有洗衣条件"],
    ["attributes", "不让陌生人进家"],
    ["money_separator", "系统不猜你的意思"],
    ["duplicates", "同一题只能有一个答案"],
  ] as const) {
    await openScenario(page, id);
    await expect(page.getByText(new RegExp(text)).first()).toBeVisible();
    await shot(page, info, `review-${id}`);
  }
});

test("没有购买（21）与部分月份没买到服务（22）", async ({ page }, info) => {
  await openScenario(page, "no_purchase");
  await expect(page.locator("[data-city]").first()).toBeVisible();
  await shot(page, info, "result-21-no-purchase");
  await openScenario(page, "partial_service");
  await expect(page.getByText(/没买到/).first()).toBeVisible();
  await shot(page, info, "fragment-22-partial-service");
});
