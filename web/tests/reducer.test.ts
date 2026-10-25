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

describe("饼图 + 滑块：精确转移", () => {
  it("主动均分保存 50/3；拖动一次把休闲的 5 点转给安全：35/3 与 65/3，其余四项不变，合计严格 100，一条撤销记录", () => {
    const eq = { keys: SIX, value: caps.points.equal_split_six };
    let s = run({ type: "points/equalSplit", group: "six_dimensions" });
    expect(s.draft.six_dimensions).toEqual({ mode: "equal_split", entries: {} });
    // 安全 50/3 → 65/3，来源休闲（均分状态下先换成精确均分值，同一条记录）
    s = draftReducer(s, { type: "points/move", group: "six_dimensions", key: "safety", value: "65/3", source: "leisure", maxDecimals: MAX, fromEqual: eq });
    expect(value(s, "six_dimensions", "safety")).toBe("65/3");
    expect(value(s, "six_dimensions", "leisure")).toBe("35/3");
    for (const k of SIX.filter((k) => k !== "safety" && k !== "leisure")) expect(value(s, "six_dimensions", k)).toBe("50/3");
    expect(total(s, "six_dimensions").equals(100)).toBe(true);
    expect(s.past).toHaveLength(2);
    s = draftReducer(s, { type: "history/undo" });
    expect(s.draft.six_dimensions.mode).toBe("equal_split");
  });

  it("从未分配池取：只改目标一项；减少退回池里；反复拖动不积累误差", () => {
    let s = run({ type: "points/move", group: "six_dimensions", key: "safety", value: "20", source: null, maxDecimals: MAX });
    expect(value(s, "six_dimensions", "safety")).toBe("20");
    expect(Object.keys(s.draft.six_dimensions.entries ?? {})).toEqual(["safety"]);
    for (let i = 0; i < 30; i += 1) {
      s = draftReducer(s, { type: "history/break" });
      s = draftReducer(s, { type: "points/move", group: "six_dimensions", key: "safety", value: i % 2 ? "20" : "65/3", source: null, maxDecimals: MAX });
    }
    expect(value(s, "six_dimensions", "safety")).toBe("20"); // 第 30 次是偶数次：回到 20，没有累积误差
    s = draftReducer(s, { type: "points/move", group: "six_dimensions", key: "safety", value: "0", source: null, maxDecimals: MAX });
    expect(value(s, "six_dimensions", "safety")).toBe("0");
  });

  it("来源不够、负数、来源等于目标：不做；连续按方向键合成一条撤销记录", () => {
    const base = run(
      { type: "points/move", group: "six_dimensions", key: "safety", value: "10", source: null, maxDecimals: MAX },
      { type: "history/break" },
      { type: "points/move", group: "six_dimensions", key: "leisure", value: "5", source: null, maxDecimals: MAX },
      { type: "history/break" },
    );
    expect(draftReducer(base, { type: "points/move", group: "six_dimensions", key: "safety", value: "20", source: "leisure", maxDecimals: MAX })).toBe(base);
    expect(draftReducer(base, { type: "points/move", group: "six_dimensions", key: "safety", value: "-1", source: null, maxDecimals: MAX })).toBe(base);
    expect(draftReducer(base, { type: "points/move", group: "six_dimensions", key: "safety", value: "12", source: "safety", maxDecimals: MAX })).toBe(base);
    let s = draftReducer(base, { type: "points/move", group: "six_dimensions", key: "safety", value: "11", source: null, maxDecimals: MAX });
    s = draftReducer(s, { type: "points/move", group: "six_dimensions", key: "safety", value: "12", source: null, maxDecimals: MAX });
    s = draftReducer(s, { type: "points/move", group: "six_dimensions", key: "safety", value: "13", source: null, maxDecimals: MAX });
    expect(s.past).toHaveLength(base.past.length + 1);
    expect(draftReducer(s, { type: "history/undo" }).draft.six_dimensions.entries?.safety?.value_text).toBe("10");
  });

  it("合计 100 还有未答：不偷偷补零；用户点“将其余设为 0”才写零值", () => {
    let s = run({ type: "points/move", group: "six_dimensions", key: "safety", value: "100", source: null, maxDecimals: MAX });
    expect(Object.keys(s.draft.six_dimensions.entries ?? {})).toEqual(["safety"]);
    s = draftReducer(s, { type: "points/setRestZero", group: "six_dimensions", keys: SIX.filter((k) => k !== "safety") });
    for (const k of SIX.filter((k) => k !== "safety")) expect(value(s, "six_dimensions", k)).toBe("0");
    expect(value(s, "six_dimensions", "safety")).toBe("100");
  });

  it("均分状态下直接改数字要经过 fromEqual；没有就不生效", () => {
    const s = run({ type: "points/equalSplit", group: "six_dimensions" });
    expect(draftReducer(s, { type: "points/text", group: "six_dimensions", key: "safety", text: "20" })).toBe(s);
    expect(draftReducer(s, { type: "points/move", group: "six_dimensions", key: "safety", value: "20", source: null, maxDecimals: MAX })).toBe(s);
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
    s = draftReducer(s, { type: "experience/set", conceptId: "occ.counter_drinks", years: null });
    expect(s.draft.experience).toEqual({ state: "provided", entries: [] });
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

  it("旧版本的文字描述：清除后回到未答，文字不再提交", () => {
    const old = { ...created.draft, leisure_interest: { state: "described" as const, text: "喜欢音乐", selected: [] } };
    const s = draftReducer(initialDraftState(old), { type: "interest/clear", field: "leisure_interest" });
    expect(s.draft.leisure_interest).toEqual({ state: "unanswered", text: null, selected: [] });
  });
});

describe("撤销与重做、城市、版本", () => {
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

  it("只有用户明确的“按新说明重新确认”才换 meta，且不带样例出处标签", () => {
    const s = run({ type: "meta/adopt", meta: { ...caps.meta, sample: { note: "x", origin: "contract_example", produced_by: "y" } } });
    expect(s.draft.meta.sample).toBeUndefined();
    expect(s.draft.meta.schema_version).toBe(caps.meta.schema_version);
  });
});
