// @vitest-environment node
// 界面导出的草稿样本：用页面同一个状态更新函数、按用户在页面上的操作生成，存在 tests/fixtures/drafts/。
// 接入层的 tests/product/test_web_drafts.py 把它们交给 E3 的 review_draft 审阅，核对 E3 读到的答案状态与界面显示的一致。
//
// 草稿的生成方式或共享结构变了，这个测试就失败：确认改动是有意的之后，用 UPDATE_DRAFT_FIXTURES=1 重新生成（再跑一遍 Python 那一侧）。
//
// 样本按接入层当前的公共结构（contracts/product/v3）生成：能力清单取 v3 的样例 01，按 v3 的 UserInputDraft 校验。页面本身还读 v2 的
// 样例（界面改接 v3 是另一项工作）；这 4 份草稿只用到六维、健康题与现金，这几处 v3 与 v2 相同，页面的状态更新函数照样适用。
// 界面改接 v3 之后，改回用 src/contract 的样例与校验。
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import { describe, expect, it } from "vitest";
import type { ProductCapabilities, UserInputDraft } from "../src/contract/types";
import { emptyDraft, groupKeys } from "../src/draft/draft";
import { type DraftAction, draftReducer, initialDraftState } from "../src/draft/reducer";

const here = path.dirname(fileURLToPath(import.meta.url));
const CONTRACT = path.resolve(here, "..", "..", "contracts", "product", "v3");
const readJson = (...parts: string[]) => JSON.parse(readFileSync(path.join(CONTRACT, ...parts), "utf8"));

const index = readJson("index.json") as { schema_version: string };
const caps = readJson("samples", "01_capabilities.json").payload as ProductCapabilities;
// 与页面的校验同样的设置：只检查、不改数据
const checkDraft = new Ajv2020({ allErrors: true, strict: true }).compile(readJson("UserInputDraft.schema.json"));
const SIX = groupKeys(caps, "six_dimensions");
const ID = "draft.web.fixture";
const FIXTURES = path.join(here, "fixtures", "drafts");

function build(...actions: DraftAction[]): UserInputDraft {
  return actions.reduce(draftReducer, initialDraftState(emptyDraft(caps, ID), ID)).draft;
}

const six = (key: string, text: string): DraftAction => ({ type: "points/text", group: "six_dimensions", key, text });
const equalThenSafety20: DraftAction[] = [
  { type: "points/equalSplit", group: "six_dimensions" },
  { type: "points/toManual", group: "six_dimensions", keys: SIX, value: caps.points.equal_split_six },
  six("safety", "20"),
];

/** 每份草稿对应端到端测试 e2e/answer-states.spec.ts 里的一段页面操作。 */
const DRAFTS: Record<string, UserInputDraft> = {
  // 休闲不填、环境选“无所谓”、物质填 0；健康第 1 题“暂不确定”、第 2 题“先跳过”
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

  it("能力清单是当前公共结构的版本", () => {
    expect(caps.meta.schema_version).toBe(index.schema_version);
  });

  it.each(Object.keys(DRAFTS))("%s 符合共享结构，并与存档的样本一致", (name) => {
    const draft = DRAFTS[name] as UserInputDraft;
    expect(checkDraft(draft), JSON.stringify(checkDraft.errors)).toBe(true);
    const stored = JSON.parse(readFileSync(path.join(FIXTURES, name), "utf8"));
    expect(stored).toEqual(draft);
  });
});
