// 造一份旧问卷（v3.3）的草稿：用服务自己的保存接口把当前草稿改写成旧版本的形状（版本字段、旧的硬要求、旧的百分比分配），
// 与 tests/product_service/test_input_closeout.py::_old_draft 同一个办法。只用于迁移交互的浏览器测试；它不能证明真正旧版产出的
// 对象身份兼容（那由 E3 的 tests/fixtures/v33_objects 与 test_legacy_objects.py 负责）。
import { expect, type Page } from "@playwright/test";

export const OLD_META = {
  schema_version: "e3a-product-schema-v3.3",
  questionnaire_version: "e3a-questionnaire-v0.3-draft",
  vocabulary_version: "e3a-vocabulary-v4",
  conversion_version: "e3a-conversion-v2",
};

export interface LegacyOptions {
  /** 再加一条待清空的要求（用于“清单变了之后旧同意作废”）。 */
  extraRequirement?: boolean;
  /** 旧的六维 / 物质百分比分配（manual）；false 就不带分配。 */
  allocations?: boolean;
}

export async function seedLegacyDraft(page: Page, draftId: string, opts: LegacyOptions = {}): Promise<void> {
  const status = await page.evaluate(
    async ({ id, extra, allocations, meta }) => {
      const session = await (await fetch("/api/v1/session")).json();
      const view = await (await fetch(`/api/v1/drafts/${id}`)).json();
      const draft = view.draft;
      Object.assign(draft.meta, meta);
      // v3.3 的对象里不能有 v3.4 / v3.5 才有的内容
      delete draft.education;
      delete draft.certificates;
      delete draft.experience;
      draft.occupation = { language: draft.occupation.language, state: "unanswered", raw_text: null, selected_candidate: null };
      draft.age = { state: "provided", value: 35 };
      draft.hard_requirements = [{ requirement_id: "hr.commute", option_id: "housing.max_commute_roundtrip", parameter_value: 60, raw_text: "我想每天往返通勤不超过一小时" }];
      if (extra) draft.hard_requirements.push({ requirement_id: "hr.second", option_id: "housing.max_commute_roundtrip", parameter_value: 45, raw_text: "第二次更新的要求：往返四十五分钟" });
      if (allocations) {
        const p = (v: string) => ({ state: "provided", value_text: v });
        draft.six_dimensions = { mode: "manual", entries: { material: p("30"), safety: p("20"), opportunity: p("20"), leisure: p("10"), environment: p("10"), commute: p("10") } };
        draft.material = { mode: "manual", entries: { housing_condition: p("25"), food_life: p("25"), daily_convenience: p("30"), personal_enjoyment: p("20") } };
      } else {
        draft.six_dimensions = { mode: null, entries: {} };
        draft.material = { mode: null, entries: {} };
      }
      const response = await fetch(`/api/v1/drafts/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf_token },
        body: JSON.stringify({ expected_revision: draft.revision, draft }),
      });
      return response.status;
    },
    { id: draftId, extra: !!opts.extraRequirement, allocations: opts.allocations !== false, meta: OLD_META },
  );
  expect(status, "用服务的保存接口写入旧版本的草稿").toBe(200);
}
