// 草稿的状态更新函数。草稿本身就是共享结构里的 UserInputDraft；另外只多记三样共享结构装不下的东西：年龄输入框里的原文
// （契约里年龄是整数）、下一个属性偏好编号、撤销记录。
//
// 规则：
// * 四种答案状态严格分开：未答 = 没有这一项；明确 0 = provided "0"；无所谓 = indifferent；暂不确定 = unsure。
// * 用户亲手输入的文字原样保存；按钮与挪分算出来的值才由这里写（能写成最多两位小数就写小数，否则写规范分数）。
// * 超过 100 不拦、不自动扣；挪分只按用户点选的那一项。
// * 撤销以“用户的一次操作”为一步：连续在同一个输入框里打字合成一步。
// * 草稿编号、修订号与 meta 归服务器：这里从不改它们（保存时由自动保存写上当时的服务器修订号）。
import type { AllocationGroup, AttributePreferenceAnswer, PointEntry, UserInputDraft } from "../contract/types";
import { Fraction, formatComputed, parsePoints } from "./exact";

export interface DraftSnapshot {
  draft: UserInputDraft;
  /** 年龄输入框里的原文（契约里年龄是整数；写不成整数时草稿里年龄记为未答，原文留在这里给用户看）。 */
  ageText: string;
  nextPrefId: number;
}

export interface DraftState extends DraftSnapshot {
  past: DraftSnapshot[];
  future: DraftSnapshot[];
  coalesceKey: string | null;
}

export type HealthChoice = "agree" | "disagree" | "no_clear_tendency" | "unsure";
export type LeisureState = NonNullable<NonNullable<UserInputDraft["leisure_interest"]>["state"]>;
export type TasteState = NonNullable<NonNullable<UserInputDraft["taste"]>["state"]>;
type InterestField = "leisure_interest" | "taste";

export type ContentAction =
  | { type: "cities/toggle"; cityRef: string; selected: boolean }
  | { type: "cities/set"; cityRefs: string[] }
  | { type: "language/set"; language: string }
  | { type: "occupation/text"; text: string }
  | { type: "occupation/select"; candidateId: string | null }
  | { type: "age/text"; text: string }
  | { type: "cash/text"; text: string; currency: string; pricePeriod: string }
  | { type: "points/text"; group: AllocationGroup; key: string; text: string }
  | { type: "points/state"; group: AllocationGroup; key: string; state: "indifferent" | "unsure" | "unanswered" }
  | { type: "points/step"; group: AllocationGroup; key: string; delta: number; maxDecimals: number }
  | { type: "points/reduce"; group: AllocationGroup; key: string; amount: string; maxDecimals: number }
  | { type: "points/transfer"; group: AllocationGroup; from: string; to: string; amount: string; maxDecimals: number }
  | { type: "points/equalSplit"; group: AllocationGroup }
  | { type: "points/toManual"; group: AllocationGroup; keys: string[]; value: string }
  | { type: "health/choice"; questionId: string; choice: HealthChoice | null }
  | { type: "interest/state"; field: InterestField; state: LeisureState | TasteState }
  | { type: "interest/text"; field: InterestField; text: string }
  | { type: "interest/select"; field: InterestField; key: string; selected: boolean }
  | { type: "freeText/text"; text: string }
  | { type: "ability/set"; abilityId: string; state: "provided" | "unsure" | null }
  | { type: "hard/set"; optionId: string; enabled: boolean; value: number | null }
  | { type: "attr/add"; kind: string; need: string; rawText: string; asHardRequirement: boolean }
  | {
      type: "attr/update";
      preferenceId: string;
      patch: Partial<Pick<AttributePreferenceAnswer, "kind" | "need" | "raw_text" | "as_hard_requirement">>;
    }
  | { type: "attr/remove"; preferenceId: string }
  /**
   * 问卷或词表换了版本：用户读过新说明、明确勾选“已按新说明重新确认”之后，才把草稿的版本信息换成当前能力清单的版本。
   * 只有这一个动作会改 meta（其余动作都不碰）；答案本身不变，之后照常重新审阅、重新确认。
   */
  | { type: "meta/adopt"; meta: UserInputDraft["meta"] };

export type DraftAction =
  | ContentAction
  | { type: "history/undo" }
  | { type: "history/redo" }
  | { type: "history/break" }
  /** 载入服务器上的一版（打开草稿、冲突时改用服务器版本）：清空撤销记录。 */
  | { type: "draft/load"; draft: UserInputDraft };

const HISTORY_LIMIT = 200;

function nextPrefIdOf(d: UserInputDraft): number {
  let max = 0;
  for (const p of d.attribute_preferences ?? []) {
    const m = /^pref\.([0-9]+)$/.exec(p.preference_id);
    if (m) max = Math.max(max, Number(m[1]));
  }
  return max + 1;
}

export function initialDraftState(draft: UserInputDraft): DraftState {
  return {
    draft,
    ageText: draft.age.value != null ? String(draft.age.value) : "",
    nextPrefId: nextPrefIdOf(draft),
    past: [],
    future: [],
    coalesceKey: null,
  };
}

function snapshotOf(s: DraftState): DraftSnapshot {
  return { draft: s.draft, ageText: s.ageText, nextPrefId: s.nextPrefId };
}

function coalesceOf(a: ContentAction): string | null {
  switch (a.type) {
    case "occupation/text":
      return "occupation.text";
    case "age/text":
      return "age";
    case "cash/text":
      return "cash";
    case "points/text":
      return `points.${a.group}.${a.key}`;
    case "interest/text":
      return `${a.field}.text`;
    case "freeText/text":
      return "free_text";
    case "hard/set":
      return `hard.${a.optionId}`;
    case "attr/update":
      return a.patch.raw_text !== undefined ? `attr.${a.preferenceId}.raw_text` : null;
    default:
      return null;
  }
}

function entriesOf(d: UserInputDraft, g: AllocationGroup): Record<string, PointEntry> {
  return { ...(d[g].entries ?? {}) };
}

function withGroup(
  d: UserInputDraft,
  g: AllocationGroup,
  mode: "manual" | "equal_split" | null,
  entries: Record<string, PointEntry>,
): UserInputDraft {
  return { ...d, [g]: { mode, entries } };
}

function providedValue(e: PointEntry | undefined, maxDecimals: number): Fraction | null {
  if (!e || e.state !== "provided" || e.value_text == null) return null;
  const p = parsePoints(e.value_text, maxDecimals);
  return p.ok ? p.value : null;
}

/** 按钮与挪分的起点：没有数字的项（未答、无所谓、暂不确定）从 0 开始；写法不对的项返回 null，不替用户改。 */
function startingValue(e: PointEntry | undefined, maxDecimals: number): Fraction | null {
  if (!e || e.state !== "provided") return new Fraction(0);
  return providedValue(e, maxDecimals);
}

function interestOf(d: UserInputDraft, field: InterestField): { state: string; text: string | null; selected: string[] } {
  const cur = d[field];
  return { state: cur?.state ?? "unanswered", text: cur?.text ?? null, selected: [...(cur?.selected ?? [])] };
}

const AGE_INT = /^[0-9]{1,3}$/;

/** 只改内容、不碰撤销记录。返回 null 表示这次操作不适用（例如均分状态下直接改数字，要先转成手动）。 */
function applyContent(s: DraftSnapshot, a: ContentAction): DraftSnapshot | null {
  const d = s.draft;
  switch (a.type) {
    case "cities/toggle": {
      const refs = d.cities.city_refs ?? [];
      const next = a.selected
        ? refs.includes(a.cityRef)
          ? refs
          : [...refs, a.cityRef]
        : refs.filter((r) => r !== a.cityRef);
      return { ...s, draft: { ...d, cities: { state: next.length ? "provided" : "unanswered", city_refs: next } } };
    }
    case "cities/set": {
      const next = [...new Set(a.cityRefs)];
      return { ...s, draft: { ...d, cities: { state: next.length ? "provided" : "unanswered", city_refs: next } } };
    }
    case "language/set":
      return { ...s, draft: { ...d, occupation: { ...d.occupation, language: a.language } } };
    case "occupation/text": {
      const text = a.text;
      const changed = text !== (d.occupation.raw_text ?? "");
      return {
        ...s,
        draft: {
          ...d,
          occupation: {
            ...d.occupation,
            state: text.trim() ? "provided" : "unanswered",
            raw_text: text === "" ? null : text,
            // 描述改了，原来选的候选不一定还对：清掉，由用户按新描述重新选（E3 也会这样提示）
            selected_candidate: changed ? null : (d.occupation.selected_candidate ?? null),
          },
        },
      };
    }
    case "occupation/select":
      return { ...s, draft: { ...d, occupation: { ...d.occupation, selected_candidate: a.candidateId } } };
    case "age/text": {
      const t = a.text.trim();
      const age = AGE_INT.test(t) ? { state: "provided" as const, value: Number(t) } : { state: "unanswered" as const, value: null };
      return { ...s, ageText: a.text, draft: { ...d, age } };
    }
    case "cash/text": {
      const empty = a.text === "";
      return {
        ...s,
        draft: {
          ...d,
          cash: {
            state: empty ? "unanswered" : "provided",
            amount_text: empty ? null : a.text,
            currency: empty ? null : a.currency,
            price_period: empty ? null : a.pricePeriod,
            meaning: "liquid_funds_v1",
          },
        },
      };
    }
    case "points/text": {
      if (d[a.group].mode === "equal_split") return null;
      const entries = entriesOf(d, a.group);
      if (a.text === "") delete entries[a.key];
      else entries[a.key] = { state: "provided", value_text: a.text };
      return { ...s, draft: withGroup(d, a.group, "manual", entries) };
    }
    case "points/state": {
      if (d[a.group].mode === "equal_split") return null;
      const entries = entriesOf(d, a.group);
      if (a.state === "unanswered") delete entries[a.key];
      else entries[a.key] = { state: a.state };
      return { ...s, draft: withGroup(d, a.group, "manual", entries) };
    }
    case "points/step": {
      if (d[a.group].mode === "equal_split") return null;
      const entries = entriesOf(d, a.group);
      const cur = startingValue(entries[a.key], a.maxDecimals);
      if (cur === null) return null;
      let next = cur.add(a.delta);
      if (next.s < 0n) next = new Fraction(0);
      entries[a.key] = { state: "provided", value_text: formatComputed(next, a.maxDecimals) };
      return { ...s, draft: withGroup(d, a.group, "manual", entries) };
    }
    case "points/reduce": {
      if (d[a.group].mode === "equal_split") return null;
      const entries = entriesOf(d, a.group);
      const cur = providedValue(entries[a.key], a.maxDecimals);
      if (cur === null) return null;
      let next = cur.sub(new Fraction(a.amount));
      if (next.s < 0n) next = new Fraction(0);
      entries[a.key] = { state: "provided", value_text: formatComputed(next, a.maxDecimals) };
      return { ...s, draft: withGroup(d, a.group, "manual", entries) };
    }
    case "points/transfer": {
      if (d[a.group].mode === "equal_split" || a.from === a.to) return null;
      const entries = entriesOf(d, a.group);
      const from = providedValue(entries[a.from], a.maxDecimals);
      if (from === null) return null;
      const want = new Fraction(a.amount);
      const moved = from.compare(want) < 0 ? from : want;
      const toCur = startingValue(entries[a.to], a.maxDecimals);
      if (toCur === null) return null;
      entries[a.from] = { state: "provided", value_text: formatComputed(from.sub(moved), a.maxDecimals) };
      entries[a.to] = { state: "provided", value_text: formatComputed(toCur.add(moved), a.maxDecimals) };
      return { ...s, draft: withGroup(d, a.group, "manual", entries) };
    }
    case "points/equalSplit":
      // E3 规定：选了平均分配就不能同时带手工数值
      return { ...s, draft: withGroup(d, a.group, "equal_split", {}) };
    case "points/toManual": {
      if (d[a.group].mode !== "equal_split") return null;
      const entries: Record<string, PointEntry> = {};
      for (const k of a.keys) entries[k] = { state: "provided", value_text: a.value };
      return { ...s, draft: withGroup(d, a.group, "manual", entries) };
    }
    case "health/choice": {
      const items = [...(d.health.items ?? [])];
      const i = items.findIndex((x) => x.question_id === a.questionId);
      const item = { question_id: a.questionId, choice: a.choice };
      if (i >= 0) items[i] = item;
      else items.push(item);
      return { ...s, draft: { ...d, health: { ...d.health, items } } };
    }
    case "interest/state": {
      const cur = interestOf(d, a.field);
      // 原文在任何作答状态下都保留；离开“用文字描述”时不再带选中的类别（撤销可以找回）
      const selected = a.state === "described" ? cur.selected : [];
      return { ...s, draft: { ...d, [a.field]: { state: a.state, text: cur.text, selected } } };
    }
    case "interest/text": {
      const cur = interestOf(d, a.field);
      const text = a.text === "" ? null : a.text;
      // 写了文字就是“用文字描述”；空白不会被当成“通用评价”
      const state = text && cur.state === "unanswered" ? "described" : cur.state;
      return { ...s, draft: { ...d, [a.field]: { state, text, selected: cur.selected } } };
    }
    case "interest/select": {
      const cur = interestOf(d, a.field);
      const has = cur.selected.includes(a.key);
      if (has === a.selected) return null;
      const selected = a.selected ? [...cur.selected, a.key] : cur.selected.filter((k) => k !== a.key);
      return { ...s, draft: { ...d, [a.field]: { state: "described", text: cur.text, selected } } };
    }
    case "freeText/text":
      return {
        ...s,
        draft: { ...d, free_text: a.text === "" ? { state: "unanswered", text: null } : { state: "provided", text: a.text } },
      };
    case "ability/set": {
      const list = (d.abilities ?? []).filter((x) => x.ability_id !== a.abilityId);
      if (a.state) list.push({ ability_id: a.abilityId, state: a.state });
      return { ...s, draft: { ...d, abilities: list } };
    }
    case "hard/set": {
      const requirementId = `hr.${a.optionId}`;
      const list = (d.hard_requirements ?? []).filter((x) => x.option_id !== a.optionId);
      // 打开这项要求时不替用户填数字：数值留空，审阅会提示要写一个数
      if (a.enabled) list.push({ requirement_id: requirementId, option_id: a.optionId, parameter_value: a.value, raw_text: null });
      return { ...s, draft: { ...d, hard_requirements: list } };
    }
    case "attr/add": {
      if (!a.rawText.trim()) return null;
      const pref: AttributePreferenceAnswer = {
        preference_id: `pref.${s.nextPrefId}`,
        need: a.need,
        kind: a.kind,
        raw_text: a.rawText,
        as_hard_requirement: a.asHardRequirement,
      };
      return { ...s, nextPrefId: s.nextPrefId + 1, draft: { ...d, attribute_preferences: [...(d.attribute_preferences ?? []), pref] } };
    }
    case "attr/update": {
      const list = (d.attribute_preferences ?? []).flatMap((p) => {
        if (p.preference_id !== a.preferenceId) return [p];
        const next = { ...p, ...a.patch };
        // 原文清空就等于删掉这一条（schema 要求原文至少 1 个字；撤销可以找回）
        return next.raw_text.trim() ? [next] : [];
      });
      return { ...s, draft: { ...d, attribute_preferences: list } };
    }
    case "attr/remove":
      return {
        ...s,
        draft: { ...d, attribute_preferences: (d.attribute_preferences ?? []).filter((p) => p.preference_id !== a.preferenceId) },
      };
    case "meta/adopt": {
      const { sample: _s, ...meta } = a.meta;
      void _s;
      return { ...s, draft: { ...d, meta } };
    }
  }
}

/** 撤销 / 重做恢复的是内容：编号、修订号与 meta 保持当前（服务器的）值。 */
function keepIdentity(target: DraftSnapshot, current: UserInputDraft): DraftSnapshot {
  return {
    ...target,
    draft: { ...target.draft, draft_id: current.draft_id, revision: current.revision, meta: current.meta },
  };
}

export function draftReducer(state: DraftState, action: DraftAction): DraftState {
  switch (action.type) {
    case "history/break":
      return state.coalesceKey === null ? state : { ...state, coalesceKey: null };
    case "history/undo": {
      const prev = state.past[state.past.length - 1];
      if (!prev) return state;
      return {
        ...state,
        ...keepIdentity(prev, state.draft),
        past: state.past.slice(0, -1),
        future: [snapshotOf(state), ...state.future],
        coalesceKey: null,
      };
    }
    case "history/redo": {
      const next = state.future[0];
      if (!next) return state;
      return {
        ...state,
        ...keepIdentity(next, state.draft),
        past: [...state.past, snapshotOf(state)],
        future: state.future.slice(1),
        coalesceKey: null,
      };
    }
    case "draft/load":
      return initialDraftState(action.draft);
    default: {
      const next = applyContent(snapshotOf(state), action);
      if (next === null) return state;
      const key = coalesceOf(action);
      const merge = key !== null && key === state.coalesceKey;
      return {
        ...state,
        ...next,
        past: merge ? state.past : [...state.past, snapshotOf(state)].slice(-HISTORY_LIMIT),
        future: [],
        coalesceKey: key,
      };
    }
  }
}
