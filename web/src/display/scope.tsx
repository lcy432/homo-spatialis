// 显示附层（e3-display-v2）的页面接法：某个业务对象的 DisplayEnvelope 放进一个作用域，<Zh> 在作用域里按原文找译文。
// * 业务状态只从 source（页面手里的业务对象）读；fields 只提供文字。页面不按顺序、不拆字符串。
// * 查找按原文：把每个 provided / missing 字段的 pointer 在 source 里取到的原文建成索引（原文 → 字段）。同一段原文在两个位置
//   给出不同译文时标为“有歧义”，照原文显示（不猜）。F2 的实体身份仍由服务保证：同名实体译文不同是极少数情形，页面不会显示错译。
// * 作用域可以嵌套（外层：能力清单；内层：审阅 / 运行意图 / 结果），由内向外找。
// * 知悉项（acknowledgements，按 ack_id）与作答状态（answer_states，按 (answer, state)）也从作用域里的信封取。
// * 历史对象用自己绑定的资源版本（envelope.resource_version）；页面不拿当前版本冒充（地图版本见 pages/results）。
import { type ReactNode, createContext, useContext, useMemo } from "react";
import type { DisplayAcknowledgement, DisplayAnswerState, DisplayEnvelope, DisplayField, DisplayText } from "../contract/types";
import { coverage, type FieldIndex, indexFields, pointerGet } from "./fields";

export interface DisplayScopeValue {
  envelope: DisplayEnvelope;
  index: FieldIndex;
  /** 原文 → 字段；null = 同一原文在不同位置译文不同（有歧义，照原文显示）。 */
  byOriginal: ReadonlyMap<string, DisplayField | null>;
}

const ScopeContext = createContext<readonly DisplayScopeValue[]>([]);

export function buildScope(envelope: DisplayEnvelope): DisplayScopeValue {
  const index = indexFields(envelope.fields);
  const byOriginal = new Map<string, DisplayField | null>();
  for (const f of envelope.fields ?? []) {
    const original = pointerGet(envelope.source, f.pointer);
    if (typeof original !== "string" || original === "") continue;
    const prev = byOriginal.get(original);
    if (prev === undefined) {
      byOriginal.set(original, f);
      continue;
    }
    if (prev === null) continue;
    // 同一原文：有译文的优先于缺译；两段不同的译文 → 有歧义
    if (prev.status === "provided" && f.status === "provided" && prev.text !== f.text) byOriginal.set(original, null);
    else if (prev.status !== "provided" && f.status === "provided") byOriginal.set(original, f);
  }
  return { envelope, index, byOriginal };
}

/** 把一个显示信封放进作用域（null / undefined = 还没有读到，子树照旧按外层或原文显示）。 */
export function DisplayScope({ envelope, children }: { envelope: DisplayEnvelope | null | undefined; children: ReactNode }) {
  const outer = useContext(ScopeContext);
  const stack = useMemo(() => (envelope ? [buildScope(envelope), ...outer] : outer), [envelope, outer]);
  return <ScopeContext.Provider value={stack}>{children}</ScopeContext.Provider>;
}

export function useDisplayScopes(): readonly DisplayScopeValue[] {
  return useContext(ScopeContext);
}

export interface LookedUp {
  /** 要显示的文字（译文，或原文）。 */
  text: string;
  /** 是否是服务给的译文。 */
  provided: boolean;
  /** 原文显示时的原因：服务报的缺译原因、ambiguous（同一原文译文不同）、null（作用域里没有这段原文）。 */
  reason: DisplayField["missing_reason"] | "ambiguous" | null;
  /** 找到的字段（有的话）。 */
  field: DisplayField | null;
}

/** 在作用域栈里按位置（优先）或原文找一段文字的译文。 */
export function lookupText(stack: readonly DisplayScopeValue[], original: string, pointer?: string): LookedUp {
  for (const s of stack) {
    const byPointer = pointer ? s.index.get(pointer) : undefined;
    const f = byPointer !== undefined ? byPointer : s.byOriginal.get(original);
    if (f === undefined) continue;
    if (f === null) return { text: original, provided: false, reason: "ambiguous", field: null };
    if (f.status === "provided" && typeof f.text === "string" && f.text !== "") return { text: f.text, provided: true, reason: null, field: f };
    return { text: original, provided: false, reason: f.missing_reason ?? "translation_missing", field: f };
  }
  return { text: original, provided: false, reason: null, field: null };
}

export function useLookup(original: string, pointer?: string): LookedUp {
  const stack = useDisplayScopes();
  return lookupText(stack, original, pointer);
}

/** 最内层作用域的覆盖情况（null = 没有作用域）。 */
export function useScopeCoverage(): { provided: number; missing: number; status: DisplayEnvelope["status"] } | null {
  const stack = useDisplayScopes();
  const s = stack[0];
  if (!s) return null;
  return { ...coverage(s.index), status: s.envelope.status };
}

/** 知悉项的说明：按 ack_id 在作用域里找（只有审阅的信封有）。 */
export function findAcknowledgement(stack: readonly DisplayScopeValue[], ackId: string): DisplayAcknowledgement | null {
  for (const s of stack) {
    const a = s.envelope.acknowledgements?.find((x) => x.ack_id === ackId);
    if (a) return a;
  }
  return null;
}

/** 作答状态的名称与含义：按 (answer, state) 找，不按顺序。 */
export function findAnswerState(stack: readonly DisplayScopeValue[], answer: DisplayAnswerState["answer"], state: string): DisplayAnswerState | null {
  for (const s of stack) {
    const a = s.envelope.answer_states?.find((x) => x.answer === answer && x.state === state);
    if (a) return a;
  }
  return null;
}

export function useAcknowledgement(ackId: string): DisplayAcknowledgement | null {
  return findAcknowledgement(useDisplayScopes(), ackId);
}

export function useAnswerState(answer: DisplayAnswerState["answer"], state: string): DisplayAnswerState | null {
  return findAnswerState(useDisplayScopes(), answer, state);
}

/** 一段 DisplayText 在某种语言下该显示什么：英文给了就用英文；否则中文原文并标明缺失原因。 */
export function resolveDisplayText(t: DisplayText | null | undefined, locale: "zh" | "en"): LookedUp | null {
  if (!t) return null;
  const zh = t.zh ?? "";
  if (locale === "zh") return zh ? { text: zh, provided: true, reason: null, field: null } : null;
  if (t.status === "provided" && t.en) return { text: t.en, provided: true, reason: null, field: null };
  if (!zh) return null;
  return { text: zh, provided: false, reason: t.missing_reason ?? "translation_missing", field: null };
}
