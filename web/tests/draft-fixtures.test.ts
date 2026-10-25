// @vitest-environment node
// 界面导出的草稿样本：从服务新建的草稿（公共样例 62）起步，用页面同一个状态更新函数、按用户在页面上的操作生成，按页面自己的
// 共享结构校验（src/contract），存在 tests/fixtures/drafts/。接入层的 tests/product/test_web_drafts.py 把它们交给 E3 的
// review_draft 审阅，核对 E3 读到的答案状态与界面显示的一致。
//
// 草稿的生成方式或共享结构变了，这个测试就失败：确认改动是有意的之后，用 UPDATE_DRAFT_FIXTURES=1 重新生成（再跑一遍 Python 那一侧）。
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
const FIXTURES = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "drafts");

function build(...actions: DraftAction[]): UserInputDraft {
  return actions.reduce(draftReducer, initialDraftState(created)).draft;
}

const six = (key: string, text: string): DraftAction => ({ type: "points/text", group: "six_dimensions", key, text });
const equalThenSafety20: DraftAction[] = [
  { type: "points/equalSplit", group: "six_dimensions" },
  { type: "points/toManual", group: "six_dimensions", keys: SIX, value: caps.points.equal_split_six },
  six("safety", "20"),
];

/** 每份草稿对应页面上的一段操作。 */
const DRAFTS: Record<string, UserInputDraft> = {
  // 休闲不填、环境选“无所谓”、物质填 0；健康第 1 题“暂不确定”、第 2 题不选（未答）
  "states.json": build(
    six("material", "0"),
    six("safety", "40"),
    six("opportunity", "30"),
    six("commute", "30"),
    { type: "points/state", group: "six_dimensions", key: "environment", state: "indifferent" },
    { type: "health/choice", questionId: "hc_more_pay_more_sick_risk", choice: "unsure" },
    { type: "health/choice", questionId: "hc_reserve_for_health_costs", choice: null },
  ),
  // 均分之后把安全改成 20：多出 10/3
  "equal-split-over.json": build(...equalThenSafety20),
  // 再从物质减去多出的 10/3：合计正好 100
  "equal-split-fixed.json": build(...equalThenSafety20, {
    type: "points/reduce",
    group: "six_dimensions",
    key: "material",
    amount: "10/3",
    maxDecimals: caps.points.manual_max_decimals,
  }),
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

  it.each(Object.keys(DRAFTS))("%s 符合共享结构，并与存档的样本一致", (name) => {
    const draft = DRAFTS[name] as UserInputDraft;
    expect(checkMessage("UserInputDraft", draft)).toEqual({ ok: true });
    const stored = JSON.parse(readFileSync(path.join(FIXTURES, name), "utf8"));
    expect(stored).toEqual(draft);
  });
});
