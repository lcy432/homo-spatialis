import { describe, expect, it } from "vitest";
import type { ProductCapabilities, UserInputDraft } from "../src/contract/types";
import { samplePayload } from "../src/contract/samples";
import { checkMessage } from "../src/contract/validate";
import { emptyDraft, groupKeys, sameDraftContent } from "../src/draft/draft";
import { type DraftAction, type DraftState, draftReducer, initialDraftState } from "../src/draft/reducer";

const caps = samplePayload<ProductCapabilities>("01");
const SIX = groupKeys(caps, "six_dimensions");

function start(): DraftState {
  return initialDraftState(emptyDraft(caps, "draft.web.test"), "draft.web.test");
}

function run(state: DraftState, ...actions: DraftAction[]): DraftState {
  return actions.reduce(draftReducer, state);
}

const text = (key: string, t: string): DraftAction => ({ type: "points/text", group: "six_dimensions", key, text: t });

describe("草稿的答案状态", () => {
  it("未答、明确 0、无所谓、暂不确定分开保存", () => {
    const s = run(
      start(),
      text("material", "0"),
      { type: "points/state", group: "six_dimensions", key: "environment", state: "indifferent" },
      { type: "points/state", group: "six_dimensions", key: "commute", state: "unsure" },
    );
    const entries = s.draft.six_dimensions.entries ?? {};
    expect(entries.leisure).toBeUndefined(); // 未答 = 没有这一项
    expect(entries.material).toEqual({ state: "provided", value_text: "0" });
    expect(entries.environment).toEqual({ state: "indifferent" });
    expect(entries.commute).toEqual({ state: "unsure" });
    expect(checkMessage("UserInputDraft", s.draft)).toEqual({ ok: true });
  });

  it("用户亲手输入的文字原样保存", () => {
    const s = run(start(), text("safety", "20.50"), {
      type: "cash/text",
      text: "3000,50",
      currency: "USD",
      pricePeriod: "2024",
    });
    expect(s.draft.six_dimensions.entries?.safety?.value_text).toBe("20.50");
    expect(s.draft.cash.amount_text).toBe("3000,50");
  });

  it("平均分配清空手工数值；之后转手动时其余各项保留精确的 50/3", () => {
    let s = run(start(), text("material", "30"), { type: "points/equalSplit", group: "six_dimensions" });
    expect(s.draft.six_dimensions).toEqual({ mode: "equal_split", entries: {} });
    // 均分状态下直接改数字不生效（界面要先问“改成手动分配吗？”）
    expect(run(s, text("safety", "20")).draft.six_dimensions.mode).toBe("equal_split");
    s = run(s, { type: "points/toManual", group: "six_dimensions", keys: SIX, value: "50/3" }, text("safety", "20"));
    const e = s.draft.six_dimensions.entries ?? {};
    expect(s.draft.six_dimensions.mode).toBe("manual");
    expect(e.safety?.value_text).toBe("20");
    for (const k of SIX.filter((k) => k !== "safety")) expect(e[k]?.value_text).toBe("50/3");
  });

  it("挪分只动用户点选的那一项：从物质挪走 10/3 之后物质是 40/3", () => {
    let s = run(
      start(),
      { type: "points/equalSplit", group: "six_dimensions" },
      { type: "points/toManual", group: "six_dimensions", keys: SIX, value: "50/3" },
      text("safety", "20"),
    );
    s = run(s, { type: "points/reduce", group: "six_dimensions", key: "material", amount: "10/3", maxDecimals: 2 });
    const e = s.draft.six_dimensions.entries ?? {};
    expect(e.material?.value_text).toBe("40/3");
    expect(e.opportunity?.value_text).toBe("50/3");
  });

  it("分满后按＋：从指定的那一项挪 5 分；那一项不够就有多少挪多少", () => {
    let s = run(start(), text("material", "3"), text("leisure", "10"));
    s = run(s, { type: "points/transfer", group: "six_dimensions", from: "material", to: "leisure", amount: "5", maxDecimals: 2 });
    const e = s.draft.six_dimensions.entries ?? {};
    expect(e.material?.value_text).toBe("0");
    expect(e.leisure?.value_text).toBe("13");
  });

  it("＋/－ 按钮：算出来的值写成小数或规范分数，不会小于 0", () => {
    let s = run(start(), text("material", "16.5"));
    s = run(s, { type: "points/step", group: "six_dimensions", key: "material", delta: 5, maxDecimals: 2 });
    expect(s.draft.six_dimensions.entries?.material?.value_text).toBe("21.5");
    s = run(s, { type: "points/step", group: "six_dimensions", key: "leisure", delta: -5, maxDecimals: 2 });
    expect(s.draft.six_dimensions.entries?.leisure?.value_text).toBe("0");
    // 写法不对的项，按钮不替用户改
    s = run(s, text("safety", "二十"), { type: "points/step", group: "six_dimensions", key: "safety", delta: 5, maxDecimals: 2 });
    expect(s.draft.six_dimensions.entries?.safety?.value_text).toBe("二十");
  });

  it("健康题：暂不确定与先跳过分开保存", () => {
    const s = run(
      start(),
      { type: "health/choice", questionId: "hc_reserve_for_health_costs", choice: "unsure" },
      { type: "health/choice", questionId: "hc_more_pay_more_sick_risk", choice: null },
    );
    expect(s.draft.health.items).toEqual([
      { question_id: "hc_reserve_for_health_costs", choice: "unsure" },
      { question_id: "hc_more_pay_more_sick_risk", choice: null },
    ]);
  });
});

describe("撤销与草稿版本", () => {
  it("连续在同一个输入框里打字合成一步撤销", () => {
    const s = run(start(), text("material", "3"), text("material", "30"), { type: "history/break" }, text("safety", "20"));
    const u1 = run(s, { type: "history/undo" });
    expect(u1.draft.six_dimensions.entries?.safety).toBeUndefined();
    expect(u1.draft.six_dimensions.entries?.material?.value_text).toBe("30");
    const u2 = run(u1, { type: "history/undo" });
    expect(u2.draft.six_dimensions.entries?.material).toBeUndefined();
    const r = run(u2, { type: "history/redo" });
    expect(r.draft.six_dimensions.entries?.material?.value_text).toBe("30");
  });

  it("revision 只增不减：撤销之后也是新编号", () => {
    const s = run(start(), text("material", "30"));
    const u = run(s, { type: "history/undo" });
    expect(u.draft.revision).toBeGreaterThan(s.draft.revision);
    const again = run(u, text("material", "40"));
    expect(again.draft.revision).toBeGreaterThan(u.draft.revision);
  });

  it("载入样例答案保持样例原样；一改就换成本标签页的草稿编号", () => {
    const sampleDraft = samplePayload<UserInputDraft>("02");
    delete (sampleDraft.meta as { sample?: unknown }).sample;
    const loaded = run(start(), { type: "draft/load", draft: sampleDraft, sampleId: "02" });
    expect(loaded.pristineSample).toBe(true);
    expect(sameDraftContent(loaded.draft, samplePayload<UserInputDraft>("02"))).toBe(true);
    const edited = run(loaded, text("leisure", "15"));
    expect(edited.pristineSample).toBe(false);
    expect(edited.draft.draft_id).toBe("draft.web.test");
  });

  it("物质为 0 时细分留在草稿里（E3 会说明不使用），改回正数还在", () => {
    const m = (key: string, t: string): DraftAction => ({ type: "points/text", group: "material", key, text: t });
    let s = run(start(), m("housing_space", "50"), m("fresh_food", "50"), m("daily_convenience", "0"), text("material", "0"));
    expect(s.draft.material.entries?.housing_space?.value_text).toBe("50");
    s = run(s, text("material", "30"));
    expect(s.draft.material.entries?.fresh_food?.value_text).toBe("50");
  });
});
