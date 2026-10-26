import { describe, expect, it } from "vitest";
import { samplePayload } from "../src/contract/samples";
import type { DraftView, ProductCapabilities } from "../src/contract/types";
import { Fraction, parsePoints } from "../src/draft/exact";
import { type DraftAction, type DraftState, draftReducer, initialDraftState } from "../src/draft/reducer";

const caps = samplePayload<ProductCapabilities>("01");
const created = samplePayload<DraftView>("62");
const SIX = caps.dimensions.map((d) => d.key);
const MAX = caps.points.manual_max_decimals;

function run(...actions: DraftAction[]): DraftState {
  return actions.reduce(draftReducer, initialDraftState(created.draft));
}

const value = (s: DraftState, g: "six_dimensions" | "material", k: string) => s.draft[g].entries?.[k]?.value_text ?? null;
const total = (s: DraftState, g: "six_dimensions" | "material") =>
  Object.values(s.draft[g].entries ?? {})
    .filter((e) => e.state === "provided" && e.value_text != null)
    .map((e) => parsePoints(e.value_text as string, MAX))
    .reduce((acc, p) => (p.ok ? acc.add(p.value) : acc), new Fraction(0));

describe("草稿状态：四种答案状态分开", () => {
  it("明确 0、无所谓、暂不确定、未答各记各的，不互相替代", () => {
    let s = run({ type: "points/text", group: "six_dimensions", key: "safety", text: "0" });
    expect(s.draft.six_dimensions.entries?.safety).toEqual({ state: "provided", value_text: "0" });
    s = draftReducer(s, { type: "points/state", group: "six_dimensions", key: "leisure", state: "indifferent" });
    expect(s.draft.six_dimensions.entries?.leisure).toEqual({ state: "indifferent" });
    s = draftReducer(s, { type: "points/state", group: "six_dimensions", key: "growth", state: "unsure" });
    expect(s.draft.six_dimensions.entries?.growth).toEqual({ state: "unsure" });
    expect(s.draft.six_dimensions.entries?.environment).toBeUndefined();
  });

  it("草稿编号、修订号与 meta 归服务器：任何编辑都不改它们", () => {
    const s = run({ type: "age/text", text: "35" }, { type: "cash/text", text: "3000", currency: "USD", pricePeriod: "2024" });
    expect(s.draft.draft_id).toBe(created.draft.draft_id);
    expect(s.draft.revision).toBe(created.draft.revision);
    expect(s.draft.meta).toEqual(created.draft.meta);
  });
});

describe("独立评分：每项 0–100，互不影响", () => {
  it("滑块一次手势写一个整数评分；其他项完全不变；同一手势合并成一条记录，下一次手势是新记录（E5-R5）", () => {
    let s = run({ type: "points/set", group: "six_dimensions", key: "safety", value: "20", gesture: "ptr-1" });
    expect(s.draft.six_dimensions).toEqual({ mode: "ratings_normalized_v1", entries: { safety: { state: "provided", value_text: "20" } } });
    // 同一次拖动的多次输入：一条记录
    s = draftReducer(s, { type: "points/set", group: "six_dimensions", key: "safety", value: "25", gesture: "ptr-1" });
    s = draftReducer(s, { type: "points/set", group: "six_dimensions", key: "safety", value: "30", gesture: "ptr-1" });
    expect(s.past).toHaveLength(1);
    // 第二次独立手势：新记录；撤销一次回到 30，再撤销回到未答
    s = draftReducer(s, { type: "points/set", group: "six_dimensions", key: "safety", value: "40", gesture: "ptr-2" });
    expect(s.past).toHaveLength(2);
    expect(value(s, "six_dimensions", "safety")).toBe("40");
    s = draftReducer(s, { type: "history/undo" });
    expect(value(s, "six_dimensions", "safety")).toBe("30");
    s = draftReducer(s, { type: "history/undo" });
    expect(s.draft.six_dimensions.entries?.safety).toBeUndefined();
    s = draftReducer(s, { type: "history/redo" });
    expect(value(s, "six_dimensions", "safety")).toBe("30");
  });

  it("连续方向键按同一编辑序列合并；失焦（history/break）后再按就是新记录", () => {
    let s = run(
      { type: "points/set", group: "six_dimensions", key: "safety", value: "20", gesture: "kb-safety" },
      { type: "points/set", group: "six_dimensions", key: "safety", value: "21", gesture: "kb-safety" },
      { type: "points/set", group: "six_dimensions", key: "safety", value: "22", gesture: "kb-safety" },
    );
    expect(s.past).toHaveLength(1);
    s = draftReducer(s, { type: "history/break" });
    s = draftReducer(s, { type: "points/set", group: "six_dimensions", key: "safety", value: "23", gesture: "kb-safety" });
    expect(s.past).toHaveLength(2);
  });

  it("80/40/40/20/10/10 原样保存为原始评分（不凑 100、不换算）；改一项不改别的项", () => {
    const vals: Record<string, string> = { material: "80", safety: "40", opportunity: "40", leisure: "20", environment: "10", commute: "10" };
    let s = run(...Object.entries(vals).map(([k, v], i) => ({ type: "points/set", group: "six_dimensions", key: k, value: v, gesture: `g${i}` }) as DraftAction));
    for (const [k, v] of Object.entries(vals)) expect(value(s, "six_dimensions", k)).toBe(v);
    expect(total(s, "six_dimensions").equals(200)).toBe(true);
    s = draftReducer(s, { type: "points/set", group: "six_dimensions", key: "safety", value: "60", gesture: "g9" });
    for (const [k, v] of Object.entries(vals)) if (k !== "safety") expect(value(s, "six_dimensions", k)).toBe(v);
    expect(value(s, "six_dimensions", "safety")).toBe("60");
  });

  it("越界、负数、分数写法、相同值：滑块动作不生效；数字框原样保存写法不对的文字（页面提示，不截断）", () => {
    const base = run({ type: "points/set", group: "six_dimensions", key: "safety", value: "10", gesture: "a" });
    expect(draftReducer(base, { type: "points/set", group: "six_dimensions", key: "safety", value: "101", gesture: "b" })).toBe(base);
    expect(draftReducer(base, { type: "points/set", group: "six_dimensions", key: "safety", value: "-1", gesture: "b" })).toBe(base);
    expect(draftReducer(base, { type: "points/set", group: "six_dimensions", key: "safety", value: "50/3", gesture: "b" })).toBe(base);
    expect(draftReducer(base, { type: "points/set", group: "six_dimensions", key: "safety", value: "10", gesture: "b" })).toBe(base);
    const typed = draftReducer(base, { type: "points/text", group: "six_dimensions", key: "safety", text: "120" });
    expect(value(typed, "six_dimensions", "safety")).toBe("120");
  });

  it("同等重视：每项 50，一条记录，可撤销；明确 0 与未答分开", () => {
    let s = run({ type: "points/set", group: "six_dimensions", key: "safety", value: "20", gesture: "a" }, { type: "points/equalWeight", group: "six_dimensions", keys: SIX, value: "50" });
    for (const k of SIX) expect(value(s, "six_dimensions", k)).toBe("50");
    expect(s.past).toHaveLength(2);
    s = draftReducer(s, { type: "history/undo" });
    expect(value(s, "six_dimensions", "safety")).toBe("20");
    expect(s.draft.six_dimensions.entries?.leisure).toBeUndefined();
    s = draftReducer(s, { type: "points/set", group: "six_dimensions", key: "leisure", value: "0", gesture: "z" });
    expect(s.draft.six_dimensions.entries?.leisure).toEqual({ state: "provided", value_text: "0" });
  });

  it("物质父项评 0：物质内部的答案原样留在草稿里（不清零、不均分）", () => {
    let s = run({ type: "points/set", group: "material", key: "housing_condition", value: "70", gesture: "m1" });
    s = draftReducer(s, { type: "points/set", group: "six_dimensions", key: "material", value: "0", gesture: "p" });
    expect(value(s, "material", "housing_condition")).toBe("70");
    s = draftReducer(s, { type: "points/set", group: "six_dimensions", key: "material", value: "30", gesture: "q" });
    expect(value(s, "material", "housing_condition")).toBe("70");
  });
});

describe("职业、资格", () => {
  it("按目录选职业：带目录版本与内容身份、不带文字；清除回到未答", () => {
    let s = run({ type: "occupation/catalog", conceptId: "occ.office_clerk", catalogVersion: "e3-input-catalog-v1", catalogFingerprint: "abc" });
    expect(s.draft.occupation).toMatchObject({ state: "provided", mode: "catalog", selected_candidate: "occ.office_clerk", catalog_version: "e3-input-catalog-v1", catalog_fingerprint: "abc", raw_text: null });
    s = draftReducer(s, { type: "occupation/clear" });
    expect(s.draft.occupation).toMatchObject({ state: "unanswered", mode: "catalog", selected_candidate: null, raw_text: null });
  });

  it("学历单选；证书：勾选 = 有、[] = 明确没有、未答与暂不确定分开；经验按职业填整年数", () => {
    let s = run({ type: "education/set", state: "provided", optionId: "bachelor" });
    expect(s.draft.education).toEqual({ state: "provided", option_id: "bachelor" });
    s = draftReducer(s, { type: "education/set", state: "unsure" });
    expect(s.draft.education).toEqual({ state: "unsure", option_id: null });
    s = draftReducer(s, { type: "certificates/toggle", abilityId: "cert.a", selected: true });
    expect(s.draft.certificates).toEqual({ state: "provided", selected: ["cert.a"] });
    s = draftReducer(s, { type: "certificates/toggle", abilityId: "cert.a", selected: false });
    expect(s.draft.certificates).toEqual({ state: "provided", selected: [] });
    s = draftReducer(s, { type: "certificates/state", state: "unanswered" });
    expect(s.draft.certificates).toEqual({ state: "unanswered", selected: [] });
    s = draftReducer(s, { type: "certificates/state", state: "none" });
    expect(s.draft.certificates).toEqual({ state: "provided", selected: [] });
    s = draftReducer(s, { type: "experience/set", conceptId: "occ.counter_drinks", years: 2 });
    expect(s.draft.experience).toEqual({ state: "provided", entries: [{ concept_id: "occ.counter_drinks", years: 2 }] });
    // 删掉最后一条年数：回到未答，不是“明确没有”（E5-R1）
    s = draftReducer(s, { type: "experience/set", conceptId: "occ.counter_drinks", years: null });
    expect(s.draft.experience).toEqual({ state: "unanswered", entries: [] });
    s = draftReducer(s, { type: "experience/state", state: "unsure" });
    expect(s.draft.experience).toEqual({ state: "unsure", entries: [] });
  });

  it("模型输入语言写进职业描述的语言；按目录选职业后语言保留", () => {
    const s = run({ type: "language/set", language: "en" }, { type: "occupation/catalog", conceptId: "occ.x", catalogVersion: "v", catalogFingerprint: "f" });
    expect(s.draft.occupation.language).toBe("en");
  });
});

describe("兴趣与口味面板", () => {
  it("点选即选中（state = selected）；全部取消回到未答；单独状态与选中互斥", () => {
    let s = run({ type: "interest/toggle", field: "taste", key: "chinese", selected: true });
    expect(s.draft.taste).toEqual({ state: "selected", text: null, selected: ["chinese"] });
    s = draftReducer(s, { type: "interest/toggle", field: "taste", key: "japanese", selected: true });
    expect(s.draft.taste?.selected).toEqual(["chinese", "japanese"]);
    s = draftReducer(s, { type: "interest/state", field: "taste", state: "no_preference" });
    expect(s.draft.taste).toEqual({ state: "no_preference", text: null, selected: [] });
    s = draftReducer(s, { type: "interest/toggle", field: "taste", key: "thai", selected: true });
    expect(s.draft.taste).toEqual({ state: "selected", text: null, selected: ["thai"] });
    s = draftReducer(s, { type: "interest/toggle", field: "taste", key: "thai", selected: false });
    expect(s.draft.taste).toEqual({ state: "unanswered", text: null, selected: [] });
  });

  it("读回的旧文字描述（described）：改回按面板选就回到未答，文字不再提交", () => {
    const old = { ...created.draft, leisure_interest: { state: "described" as const, text: "喜欢音乐", selected: [] } };
    const s = draftReducer(initialDraftState(old), { type: "interest/state", field: "leisure_interest", state: "unanswered" });
    expect(s.draft.leisure_interest).toEqual({ state: "unanswered", text: null, selected: [] });
  });
});

describe("撤销与重做、城市", () => {
  it("同一个输入框里连续打字合成一步；撤销恢复精确状态", () => {
    let s = run({ type: "age/text", text: "3" }, { type: "age/text", text: "35" });
    expect(s.past).toHaveLength(1);
    s = draftReducer(s, { type: "history/undo" });
    expect(s.ageText).toBe("");
    expect(s.draft.age.state).toBe("unanswered");
  });

  it("载入服务器的一版会清空撤销记录", () => {
    const s = draftReducer(run({ type: "age/text", text: "35" }), { type: "draft/load", draft: created.draft });
    expect(s.past).toEqual([]);
  });

  it("清除与全选是一次操作（可以撤销）", () => {
    const s = run({ type: "cities/set", cityRefs: [] });
    expect(s.draft.cities).toEqual({ state: "unanswered", city_refs: [] });
    expect(draftReducer(s, { type: "history/undo" }).draft.cities).toEqual(created.draft.cities);
  });
});
