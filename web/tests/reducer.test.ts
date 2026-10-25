import { describe, expect, it } from "vitest";
import { samplePayload } from "../src/contract/samples";
import type { DraftView, ProductCapabilities } from "../src/contract/types";
import { groupKeys } from "../src/draft/draft";
import { Fraction, remainingOf, sumProvided } from "../src/draft/exact";
import { type DraftAction, draftReducer, initialDraftState } from "../src/draft/reducer";

// 起点是服务新建的草稿（样例 62：编号与修订号由服务器分配，城市按规则预选，其余未答）
const created = samplePayload<DraftView>("62").draft;
const caps = samplePayload<ProductCapabilities>("01");
const SIX = groupKeys(caps, "six_dimensions");
const run = (...actions: DraftAction[]) => actions.reduce(draftReducer, initialDraftState(created));
const six = (key: string, text: string): DraftAction => ({ type: "points/text", group: "six_dimensions", key, text });

describe("草稿状态：四种答案状态分开", () => {
  it("明确 0、无所谓、暂不确定、未答各记各的，不互相替代", () => {
    const s = run(
      six("material", "0"),
      { type: "points/state", group: "six_dimensions", key: "environment", state: "indifferent" },
      { type: "points/state", group: "six_dimensions", key: "commute", state: "unsure" },
    );
    const e = s.draft.six_dimensions.entries ?? {};
    expect(e.material).toEqual({ state: "provided", value_text: "0" });
    expect(e.environment).toEqual({ state: "indifferent" });
    expect(e.commute).toEqual({ state: "unsure" });
    expect(e.leisure).toBeUndefined();
  });

  it("草稿编号、修订号与 meta 归服务器：任何编辑都不改它们", () => {
    const s = run(six("safety", "20"), { type: "occupation/text", text: "文员" }, { type: "history/undo" }, { type: "history/redo" });
    expect(s.draft.draft_id).toBe(created.draft_id);
    expect(s.draft.revision).toBe(created.revision);
    expect(s.draft.meta).toEqual(created.meta);
  });
});

describe("精确均分", () => {
  it("均分是主动操作；改成手动后安全改 20：精确多出 10/3（显示值 16.7 算会错成 3.5）", () => {
    const s = run(
      { type: "points/equalSplit", group: "six_dimensions" },
      { type: "points/toManual", group: "six_dimensions", keys: SIX, value: caps.points.equal_split_six },
      six("safety", "20"),
    );
    const totals = sumProvided(s.draft.six_dimensions.entries ?? {}, SIX, 2);
    expect(totals.total.equals(new Fraction(310, 3))).toBe(true);
    const rem = remainingOf(totals.total);
    expect(rem.kind).toBe("over");
    expect(rem.amount.equals(new Fraction(10, 3))).toBe(true);
    // 其余各项保留精确的 50/3，不被显示舍入覆盖，也不被等比偷扣
    for (const k of SIX.filter((x) => x !== "safety")) expect(s.draft.six_dimensions.entries?.[k]?.value_text).toBe("50/3");
  });

  it("用户亲手从物质减去多出的 10/3：物质变成 40/3，合计正好 100", () => {
    const s = run(
      { type: "points/equalSplit", group: "six_dimensions" },
      { type: "points/toManual", group: "six_dimensions", keys: SIX, value: "50/3" },
      six("safety", "20"),
      { type: "points/reduce", group: "six_dimensions", key: "material", amount: "10/3", maxDecimals: 2 },
    );
    expect(s.draft.six_dimensions.entries?.material?.value_text).toBe("40/3");
    expect(remainingOf(sumProvided(s.draft.six_dimensions.entries ?? {}, SIX, 2).total).kind).toBe("full");
  });

  it("均分状态下直接改数字不生效（要先改成手动）；＋5 写成规范分数", () => {
    const eq = run({ type: "points/equalSplit", group: "six_dimensions" });
    expect(draftReducer(eq, six("safety", "20"))).toBe(eq);
    const s = run(six("leisure", "50/3"), { type: "points/step", group: "six_dimensions", key: "leisure", delta: 5, maxDecimals: 2 });
    expect(s.draft.six_dimensions.entries?.leisure?.value_text).toBe("65/3");
  });

  it("挪分只动用户点选的那一项", () => {
    const s = run(
      six("material", "40"),
      six("safety", "30"),
      six("opportunity", "30"),
      { type: "points/transfer", group: "six_dimensions", from: "material", to: "leisure", amount: "5", maxDecimals: 2 },
    );
    const e = s.draft.six_dimensions.entries ?? {};
    expect([e.material?.value_text, e.leisure?.value_text, e.safety?.value_text, e.opportunity?.value_text]).toEqual(["35", "5", "30", "30"]);
  });
});

describe("撤销与重做", () => {
  it("同一个输入框里连续打字合成一步；撤销恢复精确状态", () => {
    const s = run(six("safety", "2"), six("safety", "20"), { type: "history/break" }, six("safety", "25"));
    expect(s.past).toHaveLength(2);
    const u = draftReducer(s, { type: "history/undo" });
    expect(u.draft.six_dimensions.entries?.safety?.value_text).toBe("20");
    const u2 = draftReducer(u, { type: "history/undo" });
    expect(u2.draft.six_dimensions.entries?.safety).toBeUndefined();
    expect(draftReducer(u2, { type: "history/redo" }).draft.six_dimensions.entries?.safety?.value_text).toBe("20");
  });

  it("载入服务器的一版会清空撤销记录", () => {
    const s = run(six("safety", "20"));
    const loaded = draftReducer(s, { type: "draft/load", draft: created });
    expect(loaded.past).toHaveLength(0);
    expect(loaded.draft.six_dimensions.entries?.safety).toBeUndefined();
  });
});

describe("城市、职业、语言", () => {
  it("清除与全选是一次操作（可以撤销）", () => {
    const cleared = run({ type: "cities/set", cityRefs: [] });
    expect(cleared.draft.cities).toEqual({ state: "unanswered", city_refs: [] });
    expect(draftReducer(cleared, { type: "history/undo" }).draft.cities).toEqual(created.cities);
  });

  it("描述改了，原来选中的职业候选清掉，由用户按新描述重新选", () => {
    const s = run({ type: "occupation/text", text: "文员" }, { type: "occupation/select", candidateId: "occ.office_clerk" });
    expect(s.draft.occupation.selected_candidate).toBe("occ.office_clerk");
    expect(draftReducer(s, { type: "occupation/text", text: "文员，也做前台" }).draft.occupation.selected_candidate).toBeNull();
  });

  it("模型输入语言写进职业描述的语言；不改原文", () => {
    const s = run({ type: "occupation/text", text: "office clerk" }, { type: "language/set", language: "en" });
    expect(s.draft.occupation.language).toBe("en");
    expect(s.draft.occupation.raw_text).toBe("office clerk");
  });
});

describe("兴趣与口味", () => {
  it("写了文字就是“用文字描述”；空白不当成通用评价；候选要用户主动选中", () => {
    const s = run({ type: "interest/text", field: "taste", text: "喜欢川菜" });
    expect(s.draft.taste).toEqual({ state: "described", text: "喜欢川菜", selected: [] });
    const picked = draftReducer(s, { type: "interest/select", field: "taste", key: "sichuan", selected: true });
    expect(picked.draft.taste?.selected).toEqual(["sichuan"]);
    const blank = run({ type: "interest/text", field: "leisure_interest", text: "" });
    expect(blank.draft.leisure_interest?.state ?? "unanswered").toBe("unanswered");
  });

  it("换成别的作答状态：原文保留，选中的类别不再带着", () => {
    const s = run(
      { type: "interest/text", field: "leisure_interest", text: "弹吉他" },
      { type: "interest/select", field: "leisure_interest", key: "music", selected: true },
      { type: "interest/state", field: "leisure_interest", state: "generic" },
    );
    expect(s.draft.leisure_interest).toEqual({ state: "generic", text: "弹吉他", selected: [] });
  });
});

describe("版本信息", () => {
  it("只有用户明确的“按新说明重新确认”才换 meta，且不带样例出处标签", () => {
    const s = run({ type: "meta/adopt", meta: caps.meta });
    expect(s.draft.meta.sample).toBeUndefined();
    expect(s.draft.meta.schema_version).toBe(caps.meta.schema_version);
  });
});
