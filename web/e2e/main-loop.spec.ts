// 主闭环：浏览器 → 真实的本地 E3C 服务 → worker → LangGraph → E1（离线 mock）。照用户故事走一遍（E3 v3.4 的输入流程）：
// 专属身份 → 开局设置 → 只留西雅图与芝加哥 → 在职业目录里搜“文员”直接选“办公室文员”→ 最高学历高中、证书“没有”（文员没有经验题）
// → 六维主动均分、池空后选来源“休闲”、键盘把安全加 5 分（65/3 与 35/3，合计精确 100，一条撤销记录）→ 物质内部均分
// → 健康、兴趣面板点“音乐”、口味搜“川菜”看到“中餐”的说明后点“中餐”（没有硬要求与属性偏好板块）
// → 审阅（目录职业、资格换算、知悉项说明来自显示信封）与确认 → 每城 1 个分身 → 等检查 → 双击“确认开始”只发一次 → 等运行结束
// → 城市比较（地图用结果绑定的显示资源版本）→ 单城详情 → 分身年度记录 → 我的运行。
import { expect, test } from "@playwright/test";
import { countRequests, createInvite, expectAccessible, saveIdentityA, shot, signIn, waitSaved } from "./helpers";

const SEATTLE = "US:CBSA2023:42660";
const CHICAGO = "US:CBSA2023:16980";

test("主闭环：从专属链接到分身的年度生活记录（真实本地服务，离线 mock）", async ({ page }, info) => {
  test.setTimeout(15 * 60_000);
  const token = createInvite("e2e 主闭环");
  const submits = countRequests(page, (url, method) => method === "POST" && url.includes("/submit"));
  const resumes = countRequests(page, (url, method) => method === "POST" && url.includes("/resume"));
  await signIn(page, token);

  // 首页：次数照服务的会话视图
  await expect(page.locator("[data-quota]")).toHaveAttribute("data-quota", "3/0/0/3");
  await expectAccessible(page, info, "home");
  await page.getByRole("link", { name: "开始一份新的填写" }).click();

  // 开局设置：显示中文、模型语言中文、稿纸
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("开始之前：语言与外观");
  await expectAccessible(page, info, "start");
  await page.getByRole("button", { name: "开始填写" }).click();
  await expect(page).toHaveURL(/\/draft\/[^/]+\/place$/);

  // 第 1 步：服务默认选好全部可选城市；清除后只留西雅图与芝加哥
  await expect(page.getByRole("status").filter({ hasText: "已选" })).toHaveText("已选：6 座");
  await page.getByRole("button", { name: "清除选择" }).click();
  await page.locator(`[data-city-ref="${SEATTLE}"] input`).check();
  await page.locator(`[data-city-ref="${CHICAGO}"] input`).check();
  await expect(page.getByRole("status").filter({ hasText: "已选" })).toHaveText("已选：2 座");
  await waitSaved(page);
  await shot(page, info, "1-place");
  await expectAccessible(page, info, "place");
  await page.getByRole("link", { name: "下一步" }).click();

  // 第 2 步：目录搜索“文员”→ 直接选“办公室文员”（搜索词不提交）；年龄、现金；最高学历高中；证书“没有”；文员没有经验题
  await page.locator("[data-occupation-search]").fill("文员");
  await expect(page.locator('[data-occupation="occ.office_clerk"]')).toBeVisible();
  await expect(page.locator("[data-no-match]")).toHaveCount(0);
  await page.locator('[data-occupation="occ.office_clerk"] button').click();
  await expect(page.locator('[data-selected-occupation="occ.office_clerk"]')).toBeVisible();
  await expect(page.locator('[data-selected-occupation="occ.office_clerk"]')).toContainText("办公室文员");
  await expect(page.locator("[data-occupation-search]")).toHaveCount(0);
  await page.locator("#age").fill("35");
  await page.locator("#cash").fill("3000");
  await page.locator('input[name="education"][value="high_school"]').check();
  await page.locator('[data-certificates-state="none"]').click();
  await expect(page.locator('[data-certificates="none"]')).toBeVisible();
  await expect(page.locator('[data-field="experience"]')).toHaveCount(0);
  await waitSaved(page);
  await shot(page, info, "2-facts");
  await expectAccessible(page, info, "facts");
  await page.getByRole("link", { name: "下一步" }).click();

  // 第 3 步：六维主动均分 → 池空，选来源“休闲”→ 键盘把安全加 5 分：安全 65/3、休闲 35/3、合计精确 100；一条撤销记录；物质内部均分
  const six = page.locator('[data-allocator="six_dimensions"]');
  await expect(six.locator('[data-slice="unallocated"]')).toHaveCount(1);
  await six.getByRole("button", { name: "各项均分" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "各项均分" }).click();
  await expect(six.getByText(/每项 16.7 分（精确值 50\/3）/)).toBeVisible();
  await expect(six.getByRole("status")).toHaveText("已分完");
  await six.locator("[data-source-select]").selectOption("leisure");
  await expect(six.locator('[data-key="safety"]')).toContainText("休闲 → 安全");
  const safety = six.locator('[data-slider="safety"]');
  await safety.focus();
  for (let i = 0; i < 5; i += 1) await page.keyboard.press("ArrowRight");
  await expect(six.locator('[data-key="safety"] [data-value]')).toHaveAttribute("data-value", "65/3");
  await expect(six.locator('[data-key="leisure"] [data-value]')).toHaveAttribute("data-value", "35/3");
  await expect(six.locator('[data-key="material"] [data-value]')).toHaveAttribute("data-value", "50/3");
  await expect(six.getByRole("status")).toHaveText("已分完");
  await expect(six.locator('[data-slice="unallocated"]')).toHaveCount(0);
  // 五次方向键是一条记录：撤销一次回到均分，重做一次回来
  await six.getByRole("button", { name: "撤销" }).click();
  await expect(six.locator('[data-key="safety"] [data-value]')).toHaveAttribute("data-value", "50/3");
  await six.getByRole("button", { name: "重做" }).click();
  await expect(six.locator('[data-key="safety"] [data-value]')).toHaveAttribute("data-value", "65/3");
  await expect(six.locator('input[name="six_dimensions.safety"]')).toHaveValue("65/3");
  const material = page.locator('[data-allocator="material"]');
  await expect(page.locator("[data-material]")).toHaveAttribute("data-material", "open");
  await material.getByRole("button", { name: "各项均分" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "各项均分" }).click();
  await expect(material.getByRole("status")).toHaveText("已分完");
  await waitSaved(page);
  await shot(page, info, "3-priorities");
  await expectAccessible(page, info, "priorities");
  await page.getByRole("link", { name: "下一步" }).click();

  // 第 4 步：健康三题（一题暂不确定）；兴趣面板点“音乐”；口味搜“川菜”：只找到“中餐”并给出说明，点“中餐”；没有硬要求与属性偏好
  await page.locator('input[name="health.items.hc_more_pay_more_sick_risk"][value="agree"]').check();
  await page.locator('input[name="health.items.hc_reserve_for_health_costs"][value="disagree"]').check();
  await page.locator('input[name="health.items.hc_wait_for_clear_risk_info"][value="unsure"]').check();
  await page.locator('input[name="leisure_interest.music"]').check();
  await expect(page.locator('[data-panel="leisure_interest"] [data-selected-key="music"]')).toBeVisible();
  const taste = page.locator('[data-panel="taste"]');
  await taste.locator("[data-panel-search]").fill("川菜");
  await expect(taste.locator("[data-panel-item]")).toHaveCount(1);
  await expect(taste.locator('[data-search-note="taste.chinese_regional"]')).toBeVisible();
  await page.locator('input[name="taste.chinese"]').check();
  await expect(taste.locator('[data-selected-key="chinese"]')).toBeVisible();
  await expect(page.locator('[data-field="hard_requirements"]')).toHaveCount(0);
  await expect(page.locator('[data-field="attribute_preferences"]')).toHaveCount(0);
  await expect(page.locator('textarea[name="leisure_interest.text"]')).toHaveCount(0);
  await waitSaved(page);
  await shot(page, info, "4-more");
  await expectAccessible(page, info, "more");
  await page.getByRole("link", { name: "下一步" }).click();

  // 第 5 步：审阅已保存的那一版 → 知悉（说明来自显示信封）→ 确认 → 每城 1 个分身 → 准备模拟
  await expect(page.locator("[data-blockers]")).toHaveAttribute("data-blockers", "0", { timeout: 60_000 });
  await expect(page.locator('[data-compare="cmp-six_dimensions"] [data-total]')).toHaveAttribute("data-total", "100");
  await expect(page.locator('[data-compare="cmp-six_dimensions"] [data-line="safety"]')).toHaveAttribute("data-exact", "65/3");
  await expect(page.locator('[data-compare="cmp-six_dimensions"] [data-line="leisure"]')).toHaveAttribute("data-exact", "35/3");
  await expect(page.locator('[data-my-occupation="occ.office_clerk"]')).toContainText("办公室文员");
  await expect(page.locator('[data-occupation-status="catalog_selected"]')).toContainText("办公室文员");
  await expect(page.locator('[data-qualifications-source="structured_answers"]')).toBeVisible();
  await expect(page.locator('[data-granted="edu_at_least_hs"]')).toBeVisible();
  await expect(page.locator('[data-requirement="edu_at_least_hs"]')).toHaveAttribute("data-met", "true");
  await expect(page.locator('[data-compare="cmp-leisure_interest"] [data-my-interest-state="selected"]')).toContainText("音乐");
  await expect(page.locator('[data-compare="cmp-taste"] [data-my-interest-state="selected"]')).toContainText("中餐");
  await expect(page.locator("[data-review-display]")).toHaveAttribute("data-review-display", /^e3-display-res-/);
  await expect(page.locator('[data-ack-source="service"]').first()).toBeVisible();
  for (const ack of await page.locator("[data-ack] input[type=checkbox]").all()) await ack.check();
  await shot(page, info, "5-review");
  await expectAccessible(page, info, "review");
  await page.getByRole("button", { name: "确认这些理解" }).click();
  await expect(page.getByRole("heading", { name: "这次模拟的规模" })).toBeVisible({ timeout: 30_000 });
  await page.locator("#opt-clones").fill("1");
  await expect(page.getByText(/它不是预计费用/)).toBeVisible();
  await page.getByRole("button", { name: /准备这次模拟/ }).click();
  await expect(page).toHaveURL(/\/intents\/[^/]+$/, { timeout: 30_000 });
  const intentId = decodeURIComponent(page.url().split("/intents/")[1] ?? "");
  saveIdentityA(token, intentId);

  // 检查：服务排队检查（不扣次数），通过后在等用户确认
  await expect(page.locator("[data-stage]")).toHaveAttribute("data-stage", "awaiting_confirmation", { timeout: 5 * 60_000 });
  await expect(page.locator("[data-preflight]")).toHaveAttribute("data-preflight", "ready");
  await expect(page.locator("[data-settings]")).toContainText("1");
  await expect(page.locator("dd[data-max-cost]")).toHaveAttribute("data-max-cost", "20");
  await shot(page, info, "6-preflight");
  await expectAccessible(page, info, "preflight");
  await page.locator('input[name="accept_scope"]').check();
  // 双击：只发一次提交
  await page.locator('[data-action="submit"]').dblclick();
  await expect(page.locator("[data-stage]")).not.toHaveAttribute("data-stage", "awaiting_confirmation", { timeout: 30_000 });
  expect(submits.get()).toBe(1);

  // 运行：轮询只读，结束后才有结果
  await expect(page.locator("[data-stage]")).toHaveAttribute("data-stage", "finished", { timeout: 10 * 60_000 });
  expect(resumes.get()).toBe(0);
  expect(submits.get()).toBe(1);
  await shot(page, info, "7-finished");
  await page.getByRole("link", { name: "查看结果" }).click();

  // 第一层：城市比较；地图用这份结果绑定的显示资源版本（不是“未绑定”，也不是当前版本冒充）
  await expect(page.locator("[data-results]")).toHaveAttribute("data-results", "generated", { timeout: 60_000 });
  await expect(page.locator('table[data-table="cities"] tbody tr')).toHaveCount(2);
  await expect(page.locator("[data-window]")).toBeVisible();
  await expect(page.getByText(/横条不是误差条或置信区间/)).toBeVisible();
  await expect(page.locator("[data-geo-version]")).toHaveAttribute("data-geo-version", /^e3-display-res-/, { timeout: 30_000 });
  await expect(page.locator('[data-map="paper"] [data-anchor]')).toHaveCount(2, { timeout: 30_000 });
  await shot(page, info, "8-results");
  await expectAccessible(page, info, "results");

  // 第二层：单城详情（西雅图）
  await page.locator(`article[data-city="${SEATTLE}"]`).getByRole("link", { name: "看这座城的详情" }).click();
  await expect(page.locator(`[data-city-page="${SEATTLE}"]`)).toBeVisible();
  await expect(page.getByText(/L：实际生活评价/)).toBeVisible();
  await expect(page.locator("[data-nature]")).toBeVisible();
  await expect(page.getByText("这次运行绑定的运行前检查读不到")).toHaveCount(0);
  await shot(page, info, "9-city");
  await expectAccessible(page, info, "city");

  // 第三层：分身的年度生活记录
  await page.getByRole("link", { name: "看这段生活" }).first().click();
  await expect(page.locator("[data-clone-page]")).toBeVisible();
  await expect(page.locator("article[data-year]")).toHaveCount(3);
  await expect(page.getByText("这一年已支付的总额（年度合计，不是月度流水）").first()).toBeVisible();
  await shot(page, info, "10-clone");
  await expectAccessible(page, info, "clone");

  // 我的运行：找得到原来的运行；刷新结果页不重新提交
  await page.getByRole("link", { name: "我的运行" }).click();
  await expect(page.locator(`[data-stage="finished"]`)).toHaveCount(1);
  await page.goto(`/results/${encodeURIComponent(intentId)}`);
  await page.reload();
  await expect(page.locator("[data-results]")).toBeVisible();
  expect(submits.get()).toBe(1);
  await page.goto("/");
  await expect(page.locator("[data-quota]")).toHaveAttribute("data-quota", "3/1/0/2");
});
