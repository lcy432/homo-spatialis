// 主闭环：浏览器 → 真实的本地 E3C 服务 → worker → LangGraph → E1（离线 mock）。照用户故事走一遍（E3 v3.5 的流程）：
// 专属身份 → 开局设置 → 只留西雅图与芝加哥 → 在职业目录里搜“文员”直接选“办公室文员”→ 最高学历高中、证书“没有”
// → 六维独立评分 80/40/40/20/10/10（不凑 100；临时预览 40/20/20/10/5/5），物质 25/25/30/20 → 健康、兴趣面板“音乐”、口味“中餐”
// → 审阅（原始评分与 E3 的精确权重分开）与确认 → 本次设置（每城 1 个分身）与一次“确认并开始”（双击只发一次、没有 submit）
// → 同一个等待页：检查 → 排队 → 模拟（按已结算的分身年度数）→ 完成；中途离开再回来仍是同一次运行 → 城市比较（地图用结果绑定的版本）
// → 单城详情 → 分身年度记录 → 我的运行 → 次数。单次运行的删除在手机项目最后一条里做（它还要先看这次的结果）。
import { expect, test } from "@playwright/test";
import { countRequests, createInvite, expectAccessible, saveIdentityA, shot, signIn, waitSaved } from "./helpers";

const SEATTLE = "US:CBSA2023:42660";
const CHICAGO = "US:CBSA2023:16980";

test("主闭环：从专属链接到分身的年度生活记录（真实本地服务，离线 mock，一次开始）", async ({ page }, info) => {
  test.setTimeout(15 * 60_000);
  const token = createInvite("e2e 主闭环");
  const resumes = countRequests(page, (url, method) => method === "POST" && url.includes("/resume"));
  const starts = countRequests(page, (url, method) => method === "POST" && /\/run-intents$/.test(new URL(url).pathname));
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

  // 第 3 步：独立评分。六维用数字框填 80/40/40/20/10/10（原始合计 200，不凑 100）；临时预览 40/20/20/10/5/5；
  // 一串方向键是一条撤销记录；改一项不改别的项；物质 25/25/30/20
  const six = page.locator('[data-allocator="six_dimensions"]');
  await expect(six).toHaveAttribute("data-preview", "none");
  await expect(six.locator('[data-key][data-state="unanswered"]')).toHaveCount(6);
  const sixVals: Record<string, string> = { material: "80", safety: "40", opportunity: "40", leisure: "20", environment: "10", commute: "10" };
  for (const [k, v] of Object.entries(sixVals)) await six.locator(`input[name="six_dimensions.${k}"]`).fill(v);
  await expect(six).toHaveAttribute("data-preview", "complete");
  await expect(six).toHaveAttribute("data-rating-total", "200");
  const weights: Record<string, string> = { material: "40", safety: "20", opportunity: "20", leisure: "10", environment: "5", commute: "5" };
  for (const [k, w] of Object.entries(weights)) await expect(six.locator(`[data-key="${k}"] [data-value]`)).toHaveAttribute("data-weight", w);
  // 键盘：安全 40 → 45（五次方向键，一条记录）；撤销一次回到 40；其他项的原始评分始终不变
  const safety = six.locator('[data-slider="safety"]');
  await safety.focus();
  for (let i = 0; i < 5; i += 1) await page.keyboard.press("ArrowRight");
  await expect(six.locator('[data-key="safety"] [data-value]')).toHaveAttribute("data-value", "45");
  await expect(six.locator('[data-key="material"] [data-value]')).toHaveAttribute("data-value", "80");
  await expect(six).toHaveAttribute("data-rating-total", "205");
  await safety.blur();
  await six.getByRole("button", { name: "撤销" }).click();
  await expect(six.locator('[data-key="safety"] [data-value]')).toHaveAttribute("data-value", "40");
  await expect(six).toHaveAttribute("data-rating-total", "200");
  const material = page.locator('[data-allocator="material"]');
  await expect(page.locator("[data-material]")).toHaveAttribute("data-material", "open");
  const matVals: Record<string, string> = { housing_condition: "25", food_life: "25", daily_convenience: "30", personal_enjoyment: "20" };
  for (const [k, v] of Object.entries(matVals)) await material.locator(`input[name="material.${k}"]`).fill(v);
  await expect(material).toHaveAttribute("data-preview", "complete");
  await expect(material.locator('[data-key="daily_convenience"] [data-value]')).toHaveAttribute("data-weight", "30");
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

  // 第 5 步：审阅已保存的那一版：原始评分与 E3 的精确权重分开；知悉（说明来自显示信封）→ 确认
  await expect(page.locator("[data-blockers]")).toHaveAttribute("data-blockers", "0", { timeout: 60_000 });
  const cmpSix = page.locator('[data-compare="cmp-six_dimensions"]');
  await expect(cmpSix.locator("[data-rating-total]")).toHaveAttribute("data-rating-total", "200");
  await expect(cmpSix.locator("[data-total]")).toHaveAttribute("data-total", "100");
  await expect(cmpSix.locator('[data-line="material"]')).toHaveAttribute("data-exact", "40");
  await expect(cmpSix.locator('[data-line="material"]')).toHaveAttribute("data-rating", "80");
  await expect(cmpSix.locator('[data-line="environment"]')).toHaveAttribute("data-exact", "5");
  await expect(cmpSix.locator('[data-allocation-rule]')).toHaveAttribute("data-allocation-rule", "ratings_normalized_v1");
  await expect(page.locator('[data-compare="cmp-material"] [data-line="daily_convenience"]')).toHaveAttribute("data-exact", "30");
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

  // 本次设置与一次“确认并开始”：每城 1 个分身 → 预览（停机上限、范围、知悉项）→ 勾选全部知悉项 → 双击只发一次；没有 submit
  const startPanel = page.locator("#start");
  await expect(startPanel).toBeVisible({ timeout: 30_000 });
  await page.locator("#opt-clones").fill("1");
  await expect(page.locator("[data-start-preview]")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("[data-start-preview]")).toHaveAttribute("data-start-allowed", "true");
  await expect(page.locator("[data-start-preview] dd[data-max-cost]")).toHaveAttribute("data-max-cost", "20");
  await expect(page.getByText(/停机上限是到了就停下的上限|离线 mock/).first()).toBeVisible();
  await expect(page.locator('[data-action="start"]')).toBeDisabled();
  const ackBoxes = await page.locator("[data-start-ack] input[type=checkbox]").all();
  expect(ackBoxes.length).toBeGreaterThanOrEqual(3);
  for (const a of ackBoxes) await a.check();
  await shot(page, info, "6-start");
  await expectAccessible(page, info, "start-panel");
  await page.locator('[data-action="start"]').dblclick();
  await expect(page).toHaveURL(/\/intents\/[^/]+$/, { timeout: 30_000 });
  const intentId = decodeURIComponent(page.url().split("/intents/")[1] ?? "");
  saveIdentityA(token, intentId);
  expect(starts.get()).toBe(1);

  // 同一个等待页：带开始授权（pending），进度条与一句话，没有第二个开始按钮
  await expect(page.locator("[data-start-state]")).toHaveAttribute("data-start-state", /pending|used/);
  await expect(page.locator("[data-phase]")).toBeVisible();
  await expect(page.locator('[data-action="submit"]')).toHaveCount(0);
  await expect(page.locator('[data-authorization]')).toBeVisible();
  await shot(page, info, "7-waiting");
  await expectAccessible(page, info, "waiting");

  // 离开再回来：首页“继续查看”回到同一次运行
  await page.getByRole("link", { name: "离开，稍后回来查看" }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.getByRole("link", { name: "继续查看" }).click();
  await expect(page).toHaveURL(new RegExp(`/intents/${intentId}$`));

  // 服务按授权自动开始并跑完：轮询只读
  await expect(page.locator("[data-phase]")).toHaveAttribute("data-phase", "finished", { timeout: 10 * 60_000 });
  await expect(page.locator("[data-start-state]")).toHaveAttribute("data-start-state", "used");
  await expect(page.locator('[data-bar="finished"]')).toHaveAttribute("data-settled", "6");
  await expect(page.locator('[data-bar="finished"]')).toHaveAttribute("data-planned", "6");
  expect(resumes.get()).toBe(0);
  expect(starts.get()).toBe(1);
  await shot(page, info, "8-finished");
  await page.getByRole("link", { name: "查看结果" }).click();

  // 第一层：城市比较；地图用这份结果绑定的显示资源版本（不是“未绑定”，也不是当前版本冒充）
  await expect(page.locator("[data-results]")).toHaveAttribute("data-results", "generated", { timeout: 60_000 });
  await expect(page.locator('table[data-table="cities"] tbody tr')).toHaveCount(2);
  await expect(page.locator("[data-window]")).toBeVisible();
  await expect(page.getByText(/横条不是误差条或置信区间/)).toBeVisible();
  await expect(page.locator("[data-geo-version]")).toHaveAttribute("data-geo-version", /^e3-display-res-/, { timeout: 30_000 });
  await expect(page.locator('[data-map="paper"] [data-anchor]')).toHaveCount(2, { timeout: 30_000 });
  await shot(page, info, "9-results");
  await expectAccessible(page, info, "results");

  // 第二层：单城详情（西雅图）
  await page.locator(`article[data-city="${SEATTLE}"]`).getByRole("link", { name: "看这座城的详情" }).click();
  await expect(page.locator(`[data-city-page="${SEATTLE}"]`)).toBeVisible();
  await expect(page.getByText(/L：实际生活评价/)).toBeVisible();
  await expect(page.locator("[data-nature]")).toBeVisible();
  await expect(page.getByText("这次运行绑定的运行前检查读不到")).toHaveCount(0);
  await shot(page, info, "10-city");
  await expectAccessible(page, info, "city");

  // 第三层：分身的年度生活记录
  await page.getByRole("link", { name: "看这段生活" }).first().click();
  await expect(page.locator("[data-clone-page]")).toBeVisible();
  await expect(page.locator("article[data-year]")).toHaveCount(3);
  await expect(page.getByText("这一年已支付的总额（年度合计，不是月度流水）").first()).toBeVisible();
  await shot(page, info, "11-clone");
  await expectAccessible(page, info, "clone");

  // 我的运行：找得到原来的运行；刷新结果页不重新提交
  await page.getByRole("link", { name: "我的运行" }).click();
  await expect(page.locator(`[data-stage="finished"]`)).toHaveCount(1);
  await page.goto(`/results/${encodeURIComponent(intentId)}`);
  await page.reload();
  await expect(page.locator("[data-results]")).toBeVisible();
  expect(starts.get()).toBe(1);
  await page.goto("/");
  await expect(page.locator("[data-quota]")).toHaveAttribute("data-quota", "3/1/0/2");
});
