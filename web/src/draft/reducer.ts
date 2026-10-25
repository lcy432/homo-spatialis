// 草稿的状态更新函数。草稿本身就是共享结构里的 UserInputDraft；另外只多记两样共享结构装不下的东西：年龄输入框里的原文
// （契约里年龄是整数）、撤销记录。
//
// 规则：
// * 四种答案状态严格分开：未答 = 没有这一项；明确 0 = provided "0"；无所谓 = indifferent；暂不确定 = unsure。
// * 职业按目录选（mode = catalog，带目录版本与内容身份，不带文字）；学历单选、证书多选（selected: [] 是明确“没有”）、经验按相关职业填整年数。
// * 兴趣与口味按面板多选（state = selected）；“暂不确定”“没有固定爱好 / 没有口味偏好”“随当地体验”是单独的状态，与选中的类别互斥。
// * 点数：用户亲手输入的文字原样保存；滑块与挪分算出来的值由这里写（能写成最多两位小数就写小数，否则写规范分数）。
//   滑块的一次拖动只产生一条记录（points/move：目标的精确新值 + 来源：未分配池或某一项）；不自动补零、不按比例扣别的项。
// * 不再收集硬要求与属性偏好（新问卷没有这两块；旧草稿由服务端迁移清空）。
// * 撤销以“用户的一次操作”为一步：连续在同一个输入框里打字、连续按方向键合成一步。
// * 草稿编号、修订号与 meta 归服务器：这里从不改它们（保存时由自动保存写上当时的服务器修订号）。
import type { AllocationGroup, PointEntry, UserInputDraft } from "../contract/types";
import { Fraction, formatComputed, parsePoints } from "./exact";

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
  /** 兼容：没有输入目录的资料包仍按文字描述 + 服务候选。 */
  | { type: "occupation/text"; text: string }
  | { type: "occupation/select"; candidateId: string | null }
  /** 新流程：从目录里点一个职业（不带文字；目录版本与内容身份随答案提交）。 */
  | { type: "occupation/catalog"; conceptId: string; catalogVersion: string; catalogFingerprint: string }
  | { type: "occupation/clear" }
  | { type: "education/set"; state: "provided"; optionId: string }
  | { type: "education/set"; state: "unanswered" | "unsure" }
  | { type: "certificates/toggle"; abilityId: string; selected: boolean }
  /** none = 明确没有证书（provided + []）。 */
  | { type: "certificates/state"; state: "none" | "unanswered" | "unsure" }
  | { type: "experience/set"; conceptId: string; years: number | null }
  | { type: "experience/state"; state: "none" | "unanswered" | "unsure" }
  | { type: "abilities/clear" }
  /** 兼容：没有输入目录时的逐项资格确认。 */
  | { type: "ability/set"; abilityId: string; state: "provided" | "unsure" | null }
  | { type: "age/text"; text: string }
  | { type: "cash/text"; text: string; currency: string; pricePeriod: string }
  | { type: "points/text"; group: AllocationGroup; key: string; text: string }
  | { type: "points/state"; group: AllocationGroup; key: string; state: "indifferent" | "unsure" | "unanswered" }
  | { type: "points/step"; group: AllocationGroup; key: string; delta: number; maxDecimals: number }
  | { type: "points/reduce"; group: AllocationGroup; key: string; amount: string; maxDecimals: number }
  | { type: "points/transfer"; group: AllocationGroup; from: string; to: string; amount: string; maxDecimals: number }
  /**
   * 滑块的一次拖动：目标项的精确新值（规范写法）。source 为 null 时差额进出“未分配”池（不改任何别的项）；
   * source 是某一项时只在这两项之间转移，来源不够就不做。
   */
  | {
      type: "points/move";
      group: AllocationGroup;
      key: string;
      value: string;
      source: string | null;
      maxDecimals: number;
      /** 均分状态下拖动：先把每项换成精确的均分值（同一条记录），再移动。 */
      fromEqual?: { keys: string[]; value: string };
    }
  /** 合计已满而还有未答项时，用户明确点“将其余项目设为 0”才写入这些零值。 */
  | { type: "points/setRestZero"; group: AllocationGroup; keys: string[] }
  | { type: "points/equalSplit"; group: AllocationGroup }
  | { type: "points/toManual"; group: AllocationGroup; keys: string[]; value: string }
  | { type: "health/choice"; questionId: string; choice: HealthChoice | null }
  /** 面板之外的单独状态，或回到未答：都清空选中的类别（撤销可以找回）。 */
  | { type: "interest/state"; field: InterestField; state: InterestSpecialState | "unanswered" }
  | { type: "interest/toggle"; field: InterestField; key: string; selected: boolean }
  | { type: "interest/clear"; field: InterestField }
  | { type: "freeText/text"; text: string }
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
    case "occupation/text":
      return "occupation.text";
    case "age/text":
      return "age";
    case "cash/text":
      return "cash";
    case "points/text":
      return `points.${a.group}.${a.key}`;
    case "points/move":
      return `points.move.${a.group}.${a.key}.${a.source ?? ""}`;
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

/** 按钮、滑块与挪分的起点：没有数字的项（未答、无所谓、暂不确定）从 0 开始；写法不对的项返回 null，不替用户改。 */
function startingValue(e: PointEntry | undefined, maxDecimals: number): Fraction | null {
  if (!e || e.state !== "provided") return new Fraction(0);
  return providedValue(e, maxDecimals);
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
    case "occupation/text": {
      const text = a.text;
      const changed = text !== (d.occupation.raw_text ?? "");
      return {
        ...s,
        draft: {
          ...d,
          occupation: {
            language: d.occupation.language,
            mode: "text",
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
      const cur = d.experience?.state === "provided" ? [...(d.experience.entries ?? [])] : [];
      const rest = cur.filter((e) => e.concept_id !== a.conceptId);
      const entries = a.years === null ? rest : [...rest, { concept_id: a.conceptId, years: a.years }];
      return { ...s, draft: { ...d, experience: { state: "provided", entries } } };
    }
    case "experience/state":
      return {
        ...s,
        draft: { ...d, experience: a.state === "none" ? { state: "provided", entries: [] } : { state: a.state, entries: [] } },
      };
    case "abilities/clear":
      return { ...s, draft: { ...d, abilities: [] } };
    case "ability/set": {
      const list = (d.abilities ?? []).filter((x) => x.ability_id !== a.abilityId);
      if (a.state) list.push({ ability_id: a.abilityId, state: a.state });
      return { ...s, draft: { ...d, abilities: list } };
    }
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
    case "points/move": {
      if (a.source === a.key) return null;
      let entries = entriesOf(d, a.group);
      if (d[a.group].mode === "equal_split") {
        if (!a.fromEqual) return null;
        entries = {};
        for (const k of a.fromEqual.keys) entries[k] = { state: "provided", value_text: a.fromEqual.value };
      }
      const cur = startingValue(entries[a.key], a.maxDecimals);
      if (cur === null) return null;
      const parsed = parsePoints(a.value, a.maxDecimals);
      if (!parsed.ok || parsed.value.s < 0n) return null;
      const next = parsed.value;
      const delta = next.sub(cur);
      if (a.source !== null) {
        const src = startingValue(entries[a.source], a.maxDecimals);
        if (src === null) return null;
        const srcNext = src.sub(delta);
        if (srcNext.s < 0n) return null;
        entries[a.source] = { state: "provided", value_text: formatComputed(srcNext, a.maxDecimals) };
      }
      entries[a.key] = { state: "provided", value_text: formatComputed(next, a.maxDecimals) };
      return { ...s, draft: withGroup(d, a.group, "manual", entries) };
    }
    case "points/setRestZero": {
      if (d[a.group].mode === "equal_split") return null;
      const entries = entriesOf(d, a.group);
      let changed = false;
      for (const k of a.keys) {
        if (!entries[k]) {
          entries[k] = { state: "provided", value_text: "0" };
          changed = true;
        }
      }
      return changed ? { ...s, draft: withGroup(d, a.group, "manual", entries) } : null;
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
    case "interest/clear":
      return { ...s, draft: { ...d, [a.field]: { state: "unanswered", text: null, selected: [] } } };
    case "freeText/text":
      return {
        ...s,
        draft: { ...d, free_text: a.text === "" ? { state: "unanswered", text: null } : { state: "provided", text: a.text } },
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
