// 旧草稿迁移（真实本地服务）：用服务的保存接口写一份旧问卷（v3.3）的草稿——带一条旧硬要求与旧的百分比分配——再在页面上迁移。
// * 要同意清除旧答案、要明确选择旧分配怎样处理（重新评分 / 同等重视）；没选不发请求。
// * 待清除的清单变了（另一次保存加了第二条要求）：原页面迁移遇到修订冲突 → 重新读取 → 旧同意作废、重新勾选（E5-R2）。
// * 英文界面：迁移说明来自这份草稿自己的显示信封（E5-R7）。
// 这只测页面的迁移交互；真正旧版产出的对象身份兼容由 E3 的 test_legacy_objects.py 用 tests/fixtures/v33_objects 验证。
import { expect, test } from "@playwright/test";
import { ORIGIN, createInvite, shot, signIn, waitSaved } from "./helpers";
import { seedLegacyDraft } from "./legacy";

async function fresh(page: import("@playwright/test").Page, label: string): Promise<string> {
  await signIn(page, createInvite(label), "/start");
  await page.getByRole("button", { name: "开始填写" }).click();
  await expect(page).toHaveURL(/\/draft\/[^/]+\/place$/);
  const id = /\/draft\/([^/]+)/.exec(page.url())?.[1] as string;
  await waitSaved(page);
  await page.goto("/");
  return id;
}

test("旧草稿：看过要清空的原文与旧的百分比分配，同意并选择“重新评分”后迁移；年龄保留、版本升到 v0.5、两组改成独立评分", async ({ page }, info) => {
  const id = await fresh(page, "e2e 迁移");
  await seedLegacyDraft(page, id);
  await page.goto(`/draft/${id}/place`);
  const panel = page.locator("[data-migration]");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("我想每天往返通勤不超过一小时");
  await expect(panel.locator('[data-legacy-allocation="six_dimensions"]')).toContainText("30");
  const migrate = panel.locator('[data-action="migrate"]');
  await expect(migrate).toBeDisabled();
  // 只同意清除、没有选择旧分配怎样处理：仍不能迁移
  await panel.locator('input[name="migration.consent"]').check();
  await expect(migrate).toBeDisabled();
  await panel.locator('input[name="migration.allocation_choice"][value="rerate"]').check();
  await expect(migrate).toBeEnabled();
  await shot(page, info, "migration-ready");
  await migrate.click();
  await expect(panel).toHaveCount(0);
  await expect(page.locator('[data-save="saved"]')).toBeVisible({ timeout: 30_000 });
  const view = await (await page.request.get(`${ORIGIN}/api/v1/drafts/${id}`)).json();
  expect(view.migration).toBeNull();
  expect(view.draft.hard_requirements).toEqual([]);
  expect(view.draft.age.value).toBe(35);
  expect(view.draft.meta.questionnaire_version).toBe("e3a-questionnaire-v0.5-draft");
  expect(view.draft.six_dimensions).toEqual({ mode: "ratings_normalized_v1", entries: {} });
  expect(view.draft.material).toEqual({ mode: "ratings_normalized_v1", entries: {} });
  // 第 3 步是独立评分控件，各项未答
  await page.goto(`/draft/${id}/priorities`);
  await expect(page.locator('[data-allocator="six_dimensions"]')).toHaveAttribute("data-preview", "none");
});

test("旧草稿：选择“同等重视”迁移后两组每项 50；审阅给出六维 50/3、物质 25", async ({ page }) => {
  const id = await fresh(page, "e2e 迁移 同等重视");
  await seedLegacyDraft(page, id);
  await page.goto(`/draft/${id}/place`);
  const panel = page.locator("[data-migration]");
  await panel.locator('input[name="migration.consent"]').check();
  await panel.locator('input[name="migration.allocation_choice"][value="equal_weight"]').check();
  await panel.locator('[data-action="migrate"]').click();
  await expect(panel).toHaveCount(0);
  await expect(page.locator('[data-save="saved"]')).toBeVisible({ timeout: 30_000 });
  await page.goto(`/draft/${id}/priorities`);
  const six = page.locator('[data-allocator="six_dimensions"]');
  await expect(six).toHaveAttribute("data-preview", "complete");
  await expect(six.locator('[data-key="safety"] [data-value]')).toHaveAttribute("data-value", "50");
  await expect(six.locator('[data-key="safety"] [data-value]')).toHaveAttribute("data-weight", "50/3");
  await page.goto(`/draft/${id}/review`);
  await expect(page.locator('[data-compare="cmp-six_dimensions"] [data-line="safety"]')).toHaveAttribute("data-exact", "50/3", { timeout: 60_000 });
  await expect(page.locator('[data-compare="cmp-material"] [data-line="food_life"]')).toHaveAttribute("data-exact", "25");
});

test("待清除的清单变了：迁移遇到修订冲突 → 重新读取 → 新清单出现、旧同意与旧选择作废（E5-R2）", async ({ page }, info) => {
  const id = await fresh(page, "e2e 迁移 冲突");
  await seedLegacyDraft(page, id);
  await page.goto(`/draft/${id}/place`);
  const panel = page.locator("[data-migration]");
  await panel.locator('input[name="migration.consent"]').check();
  await panel.locator('input[name="migration.allocation_choice"][value="rerate"]').check();
  const migrations = [] as number[];
  page.on("response", (r) => {
    if (r.url().includes("/migrate") && r.request().method() === "POST") migrations.push(r.status());
  });
  // 另一处又加了一条要清空的要求（修订号 +1）
  await seedLegacyDraft(page, id, { extraRequirement: true });
  await panel.locator('[data-action="migrate"]').click();
  await expect(panel.locator('[data-error="revision_conflict"]')).toBeVisible();
  await panel.locator('[data-action="reload-draft"]').click();
  await expect(panel).toContainText("第二次更新的要求");
  await shot(page, info, "migration-conflict-reloaded");
  await expect(panel.locator('input[name="migration.consent"]')).not.toBeChecked();
  await expect(panel.locator('input[name="migration.allocation_choice"]:checked')).toHaveCount(0);
  await expect(panel.locator('[data-action="migrate"]')).toBeDisabled();
  expect(migrations).toEqual([409]);
  const view = await (await page.request.get(`${ORIGIN}/api/v1/drafts/${id}`)).json();
  expect(view.draft.hard_requirements).toHaveLength(2);
  expect(view.draft.meta.questionnaire_version).toBe("e3a-questionnaire-v0.3-draft");
});

test("英文界面的迁移说明来自这份草稿的显示信封（E5-R7）", async ({ page }, info) => {
  const id = await fresh(page, "e2e 迁移 英文");
  await seedLegacyDraft(page, id);
  await page.locator('[data-toggle="lang-en"]').click();
  await page.goto(`/draft/${id}/place`);
  const envelope = await (await page.request.get(`${ORIGIN}/api/v1/display?kind=draft&id=${id}&locale=en`)).json();
  const field = envelope.fields.find((f: { pointer: string }) => f.pointer === "/migration/message_zh");
  expect(field?.status).toBe("provided");
  await expect(page.locator("[data-migration] .box.warn")).toHaveText(field.text, { timeout: 30_000 });
  await expect(page.locator('[data-legacy-allocation="six_dimensions"]')).toContainText("Material life");
  await shot(page, info, "migration-en");
});
