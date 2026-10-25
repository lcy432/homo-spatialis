// 输入页（E3 v3.4）：职业目录的分组、搜索、没有结果、换职业不丢其他事实；资格题（学历、证书、只在需要时问经验）；
// 饼图与滑块（初始全部未答、鼠标拖动按整数步、“将其余项目设为 0”、撤销、刷新恢复）；兴趣与口味面板的特殊状态与互斥；
// 不再有硬要求、属性偏好与文字解释；英文界面按能力清单的显示信封显示目录名称与说明。全部对着真实的本地服务。
import { expect, test } from "@playwright/test";
import { createInvite, shot, signIn, waitSaved } from "./helpers";

async function newDraft(page: import("@playwright/test").Page, label: string): Promise<string> {
  await signIn(page, createInvite(label), "/start");
  await page.getByRole("button", { name: "开始填写" }).click();
  await expect(page).toHaveURL(/\/draft\/[^/]+\/place$/);
  return page.url().replace(/\/place$/, "");
}

test("职业目录：没有结果 → 清除筛选；分组浏览；搜“程序员”直接选；换职业不丢学历，餐厅厨师才问经验", async ({ page }, info) => {
  test.setTimeout(4 * 60_000);
  const base = await newDraft(page, "e2e 目录");
  await page.goto(`${base}/facts`);
  const search = page.locator("[data-occupation-search]");
  await search.fill("教师");
  await expect(page.locator("[data-no-match]")).toBeVisible();
  await expect(page.locator("[data-no-match]")).toContainText("40 种职业");
  await expect(page.locator("[data-selected-occupation]")).toHaveCount(0);
  await page.getByRole("button", { name: "清除筛选" }).click();
  await expect(page.locator("[data-occupation]")).toHaveCount(40);
  await page.locator('[data-group="it_tech"]').click();
  await expect(page.locator("[data-occupation]")).toHaveCount(4);
  await expect(page.locator("[data-occupation]").first()).toContainText("电脑用户支持专员");
  await search.fill("软件 厨师");
  await expect(page.locator("[data-no-match]")).toBeVisible();
  await search.fill("程序员");
  await expect(page.locator("[data-occupation]")).toHaveCount(1);
  await shot(page, info, "facts-search");
  await page.locator('[data-occupation="occ.soc_15_1252"] button').click();
  await expect(page.locator('[data-selected-occupation="occ.soc_15_1252"]')).toContainText("软件开发工程师");
  await page.locator('input[name="education"][value="bachelor"]').check();
  await expect(page.locator('[data-field="experience"]')).toHaveCount(0);
  await waitSaved(page);

  // 换职业：目录重新打开、学历保留；餐厅厨师有经验题（相关职业逐个填整年数）
  await page.getByRole("button", { name: "更换职业" }).click();
  // 上次留下的分组筛选（IT 技术）还在：先回到全部分类
  await page.getByRole("button", { name: "全部分类" }).click();
  await page.locator("[data-occupation-search]").fill("厨师");
  await expect(page.locator('[data-occupation="occ.soc_35_2014"]')).toBeVisible();
  await page.locator('[data-occupation="occ.soc_35_2014"] button').click();
  await expect(page.locator('[data-selected-occupation="occ.soc_35_2014"]')).toContainText("餐厅厨师");
  await expect(page.locator('input[name="education"][value="bachelor"]')).toBeChecked();
  const exp = page.locator('[data-field="experience"]');
  await expect(exp).toBeVisible();
  await expect(exp).toContainText("至少做过 1 年");
  await exp.locator('input[name="experience.state"][value="has"]').check();
  await exp.locator('input[name="experience.occ.soc_35_3031"]').fill("2");
  await waitSaved(page);
  await shot(page, info, "facts-experience");

  // 审阅：经验换算成“餐厅厨师的相关工作经验”，开局要求满足；职业状态是目录选中
  await page.goto(`${base}/review`);
  await expect(page.locator('[data-occupation-status="catalog_selected"]')).toBeVisible({ timeout: 60_000 });
  await expect(page.locator('[data-granted="soc_35_2014_experience"]')).toBeVisible();
  await expect(page.locator('[data-requirement="soc_35_2014_experience"]')).toHaveAttribute("data-met", "true");
  await expect(page.locator('[data-granted="edu_at_least_bachelor"]')).toBeVisible();
});

test("饼图与滑块：初始全部未答；鼠标拖动按整数步、不自动补零；“将其余项目设为 0”；撤销；刷新恢复；物质为 0 不问内部", async ({ page }, info) => {
  test.setTimeout(4 * 60_000);
  const base = await newDraft(page, "e2e 饼图");
  await page.goto(`${base}/priorities`);
  const six = page.locator('[data-allocator="six_dimensions"]');
  await expect(six.locator('[data-slice="unallocated"]')).toHaveCount(1);
  await expect(six.locator('[data-key][data-state="unanswered"]')).toHaveCount(6);
  await expect(six.getByRole("status")).toHaveText("还剩 100 分未分配");
  await expect(page.locator("[data-material]")).toHaveAttribute("data-material", "need_parent");

  // 鼠标拖动安全的滑块到大约 40%：值是整数，池子按整数减少，没有累积误差；其他项仍是未答
  const slider = six.locator('[data-slider="safety"]');
  await slider.scrollIntoViewIfNeeded();
  const box = (await slider.boundingBox()) as { x: number; y: number; width: number; height: number };
  const y = box.y + box.height / 2;
  await page.mouse.move(box.x + 2, y);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.2, y, { steps: 5 });
  await page.mouse.move(box.x + box.width * 0.4, y, { steps: 5 });
  await page.mouse.up();
  const v = Number(await six.locator('[data-key="safety"] [data-value]').getAttribute("data-value"));
  expect(Number.isInteger(v) && v > 0 && v < 100).toBe(true);
  await expect(six.getByRole("status")).toHaveText(`还剩 ${100 - v} 分未分配`);
  await expect(six.locator('[data-key][data-state="unanswered"]')).toHaveCount(5);
  await expect(six.locator('[data-slice="unallocated"]')).toHaveCount(1);
  await shot(page, info, "pie-drag");

  // 精确输入：安全 80、物质 20 → 已分完，其余 4 项仍未答（不自动补零），出现“将其余 4 项设为 0”
  await six.locator('input[name="six_dimensions.safety"]').fill("80");
  await six.locator('input[name="six_dimensions.material"]').fill("20");
  await expect(six.getByRole("status")).toHaveText("已分完");
  await expect(six.locator('[data-key][data-state="unanswered"]')).toHaveCount(4);
  await expect(six.locator('[data-slice="unallocated"]')).toHaveCount(0);
  await expect(page.locator("[data-material]")).toHaveAttribute("data-material", "open");
  const restZero = six.locator('[data-action="rest-zero"]');
  await expect(restZero).toHaveText("将其余 4 项设为 0");
  await restZero.click();
  await expect(six.locator('[data-key][data-state="unanswered"]')).toHaveCount(0);
  await expect(six.locator('[data-key="leisure"] [data-value]')).toHaveAttribute("data-value", "0");
  // 撤销一次：四项回到未答；重做再设回 0
  await six.getByRole("button", { name: "撤销" }).click();
  await expect(six.locator('[data-key][data-state="unanswered"]')).toHaveCount(4);
  await six.getByRole("button", { name: "重做" }).click();
  await expect(six.locator('[data-key][data-state="unanswered"]')).toHaveCount(0);

  // 物质 0：内部四项不需要填
  await six.locator('input[name="six_dimensions.material"]').fill("0");
  await expect(page.locator("[data-material]")).toHaveAttribute("data-material", "zero");
  await six.locator('input[name="six_dimensions.material"]').fill("20");
  await expect(page.locator("[data-material]")).toHaveAttribute("data-material", "open");

  // 刷新后恢复的是服务器上保存的精确值
  await waitSaved(page);
  await page.reload();
  await expect(six.locator('[data-key="safety"] [data-value]')).toHaveAttribute("data-value", "80");
  await expect(six.locator('[data-key="material"] [data-value]')).toHaveAttribute("data-value", "20");
  await expect(six.locator('[data-key="leisure"] [data-value]')).toHaveAttribute("data-value", "0");
  await expect(six.getByRole("status")).toHaveText("已分完");
  await shot(page, info, "pie-full");
});

test("兴趣与口味面板：特殊状态与已选类别互斥（切换时说清会清空什么）；状态名称与含义来自显示信封；审阅照实显示", async ({ page }, info) => {
  test.setTimeout(4 * 60_000);
  const base = await newDraft(page, "e2e 面板");
  await page.goto(`${base}/more`);
  const taste = page.locator('[data-panel="taste"]');
  await expect(taste).toHaveAttribute("data-selected-count", "0");
  await page.locator('input[name="taste.chinese"]').check();
  await page.locator('input[name="taste.japanese"]').check();
  await expect(taste).toHaveAttribute("data-selected-count", "2");
  // 切到“没有口味偏好”（收在“其他情况”里）：先说明会清空已选的 2 项，确认后面板收起、显示状态的正式名称与含义
  await page.locator('details[data-field="taste"] summary').click();
  // 已有选中的类别：点这个单选先弹出说明，不直接改状态
  await page.locator('input[name="taste.state"][value="no_preference"]').click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("会清空已选的 2 项");
  await expect(dialog).toContainText("中餐");
  await dialog.getByRole("button", { name: "清空并改为这个状态" }).click();
  const tasteState = page.locator('[data-interest-state="no_preference"]');
  await expect(tasteState).toBeVisible();
  await expect(tasteState).toContainText("明确没有口味偏好");
  await expect(page.locator('[data-panel="taste"]')).toHaveCount(0);
  // 休闲：没有选任何类别时直接改成“暂不确定”，不弹对话框
  await page.locator('details[data-field="leisure_interest"] summary').click();
  await page.locator('input[name="leisure_interest.state"][value="unsure"]').check();
  await expect(page.locator('[data-interest-state="unsure"]')).toBeVisible();
  await expect(page.getByRole("dialog")).toBeHidden();
  // 改回按面板选：状态回到未答，面板出现
  await page.locator('[data-interest-state="unsure"]').getByRole("button", { name: "改回按面板选" }).click();
  await expect(page.locator('[data-panel="leisure_interest"]')).toHaveAttribute("data-selected-count", "0");
  await page.locator('input[name="leisure_interest.fitness"]').check();
  await page.locator('[data-panel="leisure_interest"] [data-selected-key="fitness"] button').click();
  await expect(page.locator('[data-panel="leisure_interest"]')).toHaveAttribute("data-selected-count", "0");
  await expect(page.locator('textarea[name="taste.text"]')).toHaveCount(0);
  await waitSaved(page);
  await shot(page, info, "panels");

  await page.goto(`${base}/review`);
  await expect(page.locator('[data-compare="cmp-taste"] [data-my-interest-state="no_preference"]')).toBeVisible({ timeout: 60_000 });
  await expect(page.locator('[data-compare="cmp-taste"] [data-my-interest-state="no_preference"]')).toContainText("明确没有口味偏好");
  await expect(page.locator('[data-compare="cmp-leisure_interest"] [data-my-interest-state="unanswered"]')).toBeVisible();
  await expect(page.locator('[data-compare="cmp-taste"] [data-interest-status]')).toHaveAttribute("data-interest-status", "confirmable");
});

test("英文界面：目录名称、职责、学历、面板与搜索说明来自能力清单的显示信封；审阅页用这份审阅绑定的版本", async ({ page }, info) => {
  test.setTimeout(4 * 60_000);
  const base = await newDraft(page, "e2e 英文");
  await page.locator('[data-toggle="lang-en"]').click();
  await page.goto(`${base}/facts`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Life facts");
  const search = page.locator("[data-occupation-search]");
  await search.fill("teacher");
  await expect(page.locator("[data-no-match]")).toContainText("No occupation in the catalog matches");
  await search.fill("clerk");
  await expect(page.locator('[data-occupation="occ.office_clerk"]')).toContainText("Office clerk");
  await expect(page.locator('[data-occupation="occ.office_clerk"]')).toContainText("Handling documents and spreadsheets");
  await page.locator('[data-occupation="occ.office_clerk"] button').click();
  await expect(page.locator('[data-selected-occupation="occ.office_clerk"]')).toContainText("Office clerk");
  await expect(page.locator('[data-field="education"]')).toContainText("High school");
  await page.locator('input[name="education"][value="high_school"]').check();
  // 英文完整的信封：整块的“中文原文”说明不出现
  await expect(page.locator('[role="note"][data-missing-count]')).toHaveCount(0);
  await shot(page, info, "facts-en");
  await waitSaved(page);
  await page.goto(`${base}/more`);
  await expect(page.locator('[data-panel="taste"] [data-panel-item="chinese"]')).toContainText("Chinese");
  await page.locator('[data-panel="taste"] [data-panel-search]').fill("sichuan");
  await expect(page.locator('[data-search-note="taste.chinese_regional"]')).toBeVisible();
  await waitSaved(page);
  await page.goto(`${base}/review`);
  await expect(page.locator("[data-review-display]")).toHaveAttribute("data-review-display", /^e3-display-res-/, { timeout: 60_000 });
  await expect(page.locator('[data-occupation-status="catalog_selected"]')).toContainText("Office clerk");
  await expect(page.locator('[data-granted="edu_at_least_hs"]')).toContainText("High school diploma or equivalent");
  await shot(page, info, "review-en");
});
