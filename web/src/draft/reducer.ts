// 草稿的状态更新函数。草稿本身就是共享结构里的 UserInputDraft；另外只多记两样共享结构装不下的东西：年龄输入框里的原文
// （契约里年龄是整数）、撤销记录。
//
// 规则：
// * 四种答案状态严格分开：未答 = 没有这一项；明确 0 = provided "0"；无所谓 = indifferent；暂不确定 = unsure。
// * 职业按目录选（mode = catalog，带目录版本与内容身份，不带文字）；学历单选、证书多选（selected: [] 是明确“没有”）、经验按相关职业填整年数。
// * 兴趣与口味按面板多选（state = selected）；“暂不确定”“没有固定爱好 / 没有口味偏好”“随当地体验”是单独的状态，与选中的类别互斥。
// * 评分（ratings_normalized_v1，DECISIONS/0047）：六维一组、物质四项一组，每项一个 0–100 的原始评分，互不影响、不必凑 100；
//   权重由服务按比例换算（页面只做标明为临时的预览）。用户亲手输入的文字原样保存；滑块写整数；“同等重视”把本组每项设成 50。
//   一次指针手势一条撤销记录（points/set 带手势编号：同一手势合并，下一次手势是新记录）；连续方向键按同一编辑序列合并。
//   未答不是 0；明确的 0 要用户自己给（滑块、数字框或“记为 0”）。物质父项为 0 时四项不参与，但草稿里的四项答案原样留着。
// * 不再收集硬要求与属性偏好（新问卷没有这两块）。
// * 撤销以“用户的一次操作”为一步：连续在同一个输入框里打字、连续按方向键合成一步。
// * 草稿编号、修订号与 meta 归服务器：这里从不改它们（保存时由自动保存写上当时的服务器修订号）。
// * 只适配当前问卷：没有文字描述职业、逐项资格确认或兴趣文字这些旧流程的动作。
import type { AllocationGroup, PointEntry, UserInputDraft } from "../contract/types";
import { Fraction, parsePoints } from "./exact";

/** 评分的范围与精度（与能力清单 points.ratings 相同：0–100、最多两位小数）；页面也会读能力清单，这里是最后一道形状检查。 */
const RATING_MAX = new Fraction(100);
const RATING_MAX_DECIMALS = 2;

export interface DraftSnapshot {
  draft: UserInputDraft;
  /** 年龄输入框里的原文（契约里年龄是整数；写不成整数时草稿里年龄记为未答，原文留在这里给用户看）。 */
  ageText: string;
}

export interface DraftState extends DraftSnapshot {
  past: DraftSnapshot[];
  future: DraftSnapshot[];
  coalesceKey: string | null;
}

export type HealthChoice = "agree" | "disagree" | "no_clear_tendency" | "unsure";
export type LeisureState = NonNullable<NonNullable<UserInputDraft["leisure_interest"]>["state"]>;
export type TasteState = NonNullable<NonNullable<UserInputDraft["taste"]>["state"]>;
export type InterestField = "leisure_interest" | "taste";
/** 面板之外的单独状态（与选中的类别互斥）。 */
export type InterestSpecialState = "unsure" | "no_fixed_hobby" | "no_preference" | "generic";

export type ContentAction =
  | { type: "cities/toggle"; cityRef: string; selected: boolean }
  | { type: "cities/set"; cityRefs: string[] }
  | { type: "language/set"; language: string }
  /** 从目录里点一个职业（不带文字；目录版本与内容身份随答案提交）。 */
  | { type: "occupation/catalog"; conceptId: string; catalogVersion: string; catalogFingerprint: string }
  | { type: "occupation/clear" }
  | { type: "education/set"; state: "provided"; optionId: string }
  | { type: "education/set"; state: "unanswered" | "unsure" }
  | { type: "certificates/toggle"; abilityId: string; selected: boolean }
  /** none = 明确没有证书（provided + []）。 */
  | { type: "certificates/state"; state: "none" | "unanswered" | "unsure" }
  | { type: "experience/set"; conceptId: string; years: number | null }
  | { type: "experience/state"; state: "none" | "unanswered" | "unsure" }
  | { type: "age/text"; text: string }
  | { type: "cash/text"; text: string; currency: string; pricePeriod: string }
  /** 数字框里亲手输入的文字：原样保存（空字符串 = 回到未答）。 */
  | { type: "points/text"; group: AllocationGroup; key: string; text: string }
  | { type: "points/state"; group: AllocationGroup; key: string; state: "indifferent" | "unsure" | "unanswered" }
  /**
   * 滑块或按钮算出来的评分（规范写法，0–100）。gesture 是这次手势的编号：同一手势的多次输入合并成一条撤销记录，
   * 下一次独立手势（新的编号）是新记录；连续方向键用同一个编号，失焦或换项时结束。
   */
  | { type: "points/set"; group: AllocationGroup; key: string; value: string; gesture: string }
  /** “同等重视”：用户主动把本组每项的原始评分设成 value（服务规定 50）；一条记录。 */
  | { type: "points/equalWeight"; group: AllocationGroup; keys: string[]; value: string }
  | { type: "health/choice"; questionId: string; choice: HealthChoice | null }
  /** 面板之外的单独状态，或回到未答：都清空选中的类别（撤销可以找回）。 */
  | { type: "interest/state"; field: InterestField; state: InterestSpecialState | "unanswered" }
  | { type: "interest/toggle"; field: InterestField; key: string; selected: boolean }
  | { type: "freeText/text"; text: string };

export type DraftAction =
  | ContentAction
  | { type: "history/undo" }
  | { type: "history/redo" }
  | { type: "history/break" }
  /** 载入服务器上的一版（打开草稿、冲突时改用服务器版本、迁移之后）：清空撤销记录。 */
  | { type: "draft/load"; draft: UserInputDraft };

const HISTORY_LIMIT = 200;

export function initialDraftState(draft: UserInputDraft): DraftState {
  return {
    draft,
    ageText: draft.age.value != null ? String(draft.age.value) : "",
    past: [],
    future: [],
    coalesceKey: null,
  };
}

function snapshotOf(s: DraftState): DraftSnapshot {
  return { draft: s.draft, ageText: s.ageText };
}

function coalesceOf(a: ContentAction): string | null {
  switch (a.type) {
    case "age/text":
      return "age";
    case "cash/text":
      return "cash";
    case "points/text":
      return `points.${a.group}.${a.key}`;
    case "points/set":
      return `points.set.${a.group}.${a.key}.${a.gesture}`;
    case "experience/set":
      return `experience.${a.conceptId}`;
    case "freeText/text":
      return "free_text";
    default:
      return null;
  }
}

function entriesOf(d: UserInputDraft, g: AllocationGroup): Record<string, PointEntry> {
  return { ...(d[g].entries ?? {}) };
}

const RATINGS_MODE = "ratings_normalized_v1";

function withGroup(d: UserInputDraft, g: AllocationGroup, entries: Record<string, PointEntry>): UserInputDraft {
  return { ...d, [g]: { mode: RATINGS_MODE, entries } };
}

function interestOf(d: UserInputDraft, field: InterestField): { state: string; selected: string[] } {
  const cur = d[field];
  return { state: cur?.state ?? "unanswered", selected: [...(cur?.selected ?? [])] };
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
    case "occupation/catalog":
      return {
        ...s,
        draft: {
          ...d,
          occupation: {
            language: d.occupation.language,
            state: "provided",
            mode: "catalog",
            raw_text: null,
            selected_candidate: a.conceptId,
            catalog_version: a.catalogVersion,
            catalog_fingerprint: a.catalogFingerprint,
          },
        },
      };
    case "occupation/clear":
      return {
        ...s,
        draft: { ...d, occupation: { language: d.occupation.language, state: "unanswered", mode: "catalog", raw_text: null, selected_candidate: null } },
      };
    case "education/set":
      return {
        ...s,
        draft: { ...d, education: a.state === "provided" ? { state: "provided", option_id: a.optionId } : { state: a.state, option_id: null } },
      };
    case "certificates/toggle": {
      const cur = d.certificates?.state === "provided" ? [...(d.certificates.selected ?? [])] : [];
      const has = cur.includes(a.abilityId);
      if (has === a.selected) return null;
      const selected = a.selected ? [...cur, a.abilityId] : cur.filter((k) => k !== a.abilityId);
      return { ...s, draft: { ...d, certificates: { state: "provided", selected } } };
    }
    case "certificates/state":
      return {
        ...s,
        draft: { ...d, certificates: a.state === "none" ? { state: "provided", selected: [] } : { state: a.state, selected: [] } },
      };
    case "experience/set": {
      // 年数只有写成非负整数才成为答案；删掉最后一条就回到未答（“明确没有相关经验”只来自用户点那个单选）
      const cur = d.experience?.state === "provided" ? [...(d.experience.entries ?? [])] : [];
      const rest = cur.filter((e) => e.concept_id !== a.conceptId);
      const entries = a.years === null ? rest : [...rest, { concept_id: a.conceptId, years: a.years }];
      return { ...s, draft: { ...d, experience: entries.length ? { state: "provided", entries } : { state: "unanswered", entries: [] } } };
    }
    case "experience/state":
      return {
        ...s,
        draft: { ...d, experience: a.state === "none" ? { state: "provided", entries: [] } : { state: a.state, entries: [] } },
      };
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
      const entries = entriesOf(d, a.group);
      if (a.text === "") delete entries[a.key];
      else entries[a.key] = { state: "provided", value_text: a.text };
      return { ...s, draft: withGroup(d, a.group, entries) };
    }
    case "points/state": {
      const entries = entriesOf(d, a.group);
      if (a.state === "unanswered") delete entries[a.key];
      else entries[a.key] = { state: a.state };
      return { ...s, draft: withGroup(d, a.group, entries) };
    }
    case "points/set": {
      // 评分写十进制（分数写法“50/3”不是评分，服务会拒绝），范围 0–100
      const parsed = parsePoints(a.value, RATING_MAX_DECIMALS);
      if (a.value.includes("/") || !parsed.ok || parsed.value.s < 0n || parsed.value.compare(RATING_MAX) > 0) return null;
      const entries = entriesOf(d, a.group);
      const cur = entries[a.key];
      if (cur?.state === "provided" && cur.value_text === a.value) return null;
      entries[a.key] = { state: "provided", value_text: a.value };
      return { ...s, draft: withGroup(d, a.group, entries) };
    }
    case "points/equalWeight": {
      const entries: Record<string, PointEntry> = {};
      for (const k of a.keys) entries[k] = { state: "provided", value_text: a.value };
      return { ...s, draft: withGroup(d, a.group, entries) };
    }
    case "health/choice": {
      const items = [...(d.health.items ?? [])];
      const i = items.findIndex((x) => x.question_id === a.questionId);
      const item = { question_id: a.questionId, choice: a.choice };
      if (i >= 0) items[i] = item;
      else items.push(item);
      return { ...s, draft: { ...d, health: { ...d.health, items } } };
    }
    case "interest/state":
      // 单独状态与选中的类别互斥；回到未答也清空（旧版本的文字描述一并去掉）
      return { ...s, draft: { ...d, [a.field]: { state: a.state, text: null, selected: [] } } };
    case "interest/toggle": {
      const cur = interestOf(d, a.field);
      const base = cur.state === "selected" ? cur.selected : [];
      const has = base.includes(a.key);
      if (has === a.selected) return null;
      const selected = a.selected ? [...base, a.key] : base.filter((k) => k !== a.key);
      return { ...s, draft: { ...d, [a.field]: selected.length ? { state: "selected", text: null, selected } : { state: "unanswered", text: null, selected: [] } } };
    }
    case "freeText/text":
      return {
        ...s,
        draft: { ...d, free_text: a.text === "" ? { state: "unanswered", text: null } : { state: "provided", text: a.text } },
      };
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
