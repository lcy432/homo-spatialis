// @vitest-environment node
// 界面导出的草稿样本：从服务新建的草稿（公共样例 62）起步，用页面同一个状态更新函数、按用户在页面上的操作生成，按页面自己的
// 共享结构校验（src/contract），存在 tests/fixtures/drafts/。接入层的 tests/product/test_web_drafts.py 把它们交给 E3 的
// review_draft 审阅，核对 E3 读到的答案状态与界面显示的一致。
//
// 草稿的生成方式或共享结构变了，这个测试就失败：确认改动是有意的之后，用 UPDATE_DRAFT_FIXTURES=1 重新生成（再跑一遍 Python 那一侧）。
// v3.5 起六维与物质是独立评分（ratings_normalized_v1，DECISIONS/0047）：样本里是原始评分，权重由 E3 审阅换算。
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { samplePayload } from "../src/contract/samples";
import { CONTRACT_INDEX } from "../src/contract/schemas";
import type { DraftView, ProductCapabilities, UserInputDraft } from "../src/contract/types";
import { checkMessage } from "../src/contract/validate";
import { groupKeys } from "../src/draft/draft";
import { type DraftAction, draftReducer, initialDraftState } from "../src/draft/reducer";

const caps = samplePayload<ProductCapabilities>("01");
const created = samplePayload<DraftView>("62").draft;
delete (created.meta as { sample?: unknown }).sample;
const SIX = groupKeys(caps, "six_dimensions");
const MAT = groupKeys(caps, "material");
const FIXTURES = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "drafts");

function build(...actions: DraftAction[]): UserInputDraft {
  return actions.reduce(draftReducer, initialDraftState(created)).draft;
}

let g = 0;
/** 滑块的一次手势（整数评分）。 */
const slide = (group: "six_dimensions" | "material", key: string, value: string): DraftAction => ({ type: "points/set", group, key, value, gesture: `g${(g += 1)}` });
/** 数字框里亲手输入的文字。 */
const typed = (group: "six_dimensions" | "material", key: string, text: string): DraftAction => ({ type: "points/text", group, key, text });

/** 每份草稿对应页面上的一段操作。 */
const DRAFTS: Record<string, UserInputDraft> = {
  // 主故事：六维滑块 80/40/40/20/10/10（不凑 100；E3 换成 40/20/20/10/5/5），物质用数字框 25/25/30/20
  "ratings.json": build(
    slide("six_dimensions", "material", "80"),
    slide("six_dimensions", "safety", "40"),
    slide("six_dimensions", "opportunity", "40"),
    slide("six_dimensions", "leisure", "20"),
    slide("six_dimensions", "environment", "10"),
    slide("six_dimensions", "commute", "10"),
    typed("material", "housing_condition", "25"),
    typed("material", "food_life", "25"),
    typed("material", "daily_convenience", "30"),
    typed("material", "personal_enjoyment", "20"),
  ),
  // 状态分开：物质明确 0（“记为 0”）、安全 40、发展 30、通勤 30、环境“无所谓”、休闲不填；健康第 1 题“暂不确定”、第 2 题不选
  "states.json": build(
    slide("six_dimensions", "material", "0"),
    typed("six_dimensions", "safety", "40"),
    typed("six_dimensions", "opportunity", "30"),
    typed("six_dimensions", "commute", "30"),
    { type: "points/state", group: "six_dimensions", key: "environment", state: "indifferent" },
    { type: "health/choice", questionId: "hc_more_pay_more_sick_risk", choice: "unsure" },
    { type: "health/choice", questionId: "hc_reserve_for_health_costs", choice: null },
  ),
  // 两组都点“同等重视”：每项 50（E3：六维每项 50/3，物质每项 25）
  "equal-weight.json": build({ type: "points/equalWeight", group: "six_dimensions", keys: SIX, value: "50" }, { type: "points/equalWeight", group: "material", keys: MAT, value: "50" }),
  // 物质父项为 0：四项不适用，但草稿里已填的物质内部答案留着（居住 70、日常 0、享受 30，饮食未答）
  "parent-zero.json": build(
    slide("material", "housing_condition", "70"),
    slide("material", "daily_convenience", "0"),
    slide("material", "personal_enjoyment", "30"),
    slide("six_dimensions", "material", "0"),
    slide("six_dimensions", "safety", "40"),
    slide("six_dimensions", "opportunity", "20"),
    slide("six_dimensions", "leisure", "10"),
    slide("six_dimensions", "environment", "10"),
    slide("six_dimensions", "commute", "10"),
  ),
  // 金额写成“3000,50”：原样保存，交给 E3 判断
  "money-comma.json": build({ type: "cash/text", text: "3000,50", currency: "USD", pricePeriod: "2024" }),
};

describe("界面导出的草稿样本（给接入层核对）", () => {
  if (process.env.UPDATE_DRAFT_FIXTURES === "1") {
    mkdirSync(FIXTURES, { recursive: true });
    for (const [name, draft] of Object.entries(DRAFTS)) {
      writeFileSync(path.join(FIXTURES, name), `${JSON.stringify(draft, null, 2)}\n`, "utf8");
    }
  }

  it("起点是当前共享结构下服务新建的草稿", () => {
    expect(created.meta.schema_version).toBe(CONTRACT_INDEX.schema_version);
  });

  it("主故事的草稿里是原始评分，不是换算后的权重", () => {
    const d = DRAFTS["ratings.json"] as UserInputDraft;
    expect(d.six_dimensions.mode).toBe("ratings_normalized_v1");
    expect(d.six_dimensions.entries?.material).toEqual({ state: "provided", value_text: "80" });
    expect(d.material.entries?.daily_convenience).toEqual({ state: "provided", value_text: "30" });
  });

  it.each(Object.keys(DRAFTS))("%s 符合共享结构，并与存档的样本一致", (name) => {
    const draft = DRAFTS[name] as UserInputDraft;
    expect(checkMessage("UserInputDraft", draft)).toEqual({ ok: true });
    const stored = JSON.parse(readFileSync(path.join(FIXTURES, name), "utf8"));
    expect(stored).toEqual(draft);
  });
});
