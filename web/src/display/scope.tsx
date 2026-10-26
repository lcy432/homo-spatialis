// 显示附层（e3-display-v2）的页面接法：某个业务对象的 DisplayEnvelope 放进一个作用域，<Zh pointer=…> 按字段位置取译文。
// * 业务状态只从 source（页面手里的业务对象）读；fields 只提供文字。页面不按顺序、不拆字符串、不按中文原文反查。
// * 定位靠 JSON Pointer + 来源核对：某个位置的字段只在“信封的 source 在同一位置就是页面正在显示的这段原文”时才算数
//   （独立核查 E5-R3：同名字段各取自己的译文，缺译不借另一个字段，数组重排不串，附层与业务对象不匹配就不套文字）。
// * 作用域可以嵌套（外层：当前的会话与能力清单；内层：审阅 / 草稿 / 运行意图 / 结果等历史或当场对象）。由内向外找：
//   内层的 source 在这个位置有同样的原文，就以内层为准——内层缺译照样显示原文并说明原因，不借外层当前资源的文字；
//   内层的 source 根本没有这个位置（不同形状的对象）才看外层。
// * 知悉项（acknowledgements，按 ack_id）与作答状态（answer_states，按 (answer, state)）也从作用域里的信封取。
// * 每个作用域记下实际显示过的、没有译文的位置：整块的“中文原文”说明按页面真正显示的字段数写，不按服务的缺失计数。
import { type ReactNode, createContext, useContext, useEffect, useMemo, useSyncExternalStore } from "react";
import type { DisplayAcknowledgement, DisplayAnswerState, DisplayAttachment, DisplayEnvelope, DisplayField, DisplayText } from "../contract/types";
import { type FieldIndex, indexFields, pointerGet } from "./fields";

/** 页面能放进作用域的信封：正式的 DisplayEnvelope，或即时回复（预览、错误）的附层配上它的业务对象。 */
export interface ScopeEnvelope {
  source: unknown;
  fields: readonly DisplayField[];
  status: DisplayEnvelope["status"];
  resource_version: string | null;
  acknowledgements: readonly DisplayAcknowledgement[];
  answer_states: readonly DisplayAnswerState[];
}

export interface DisplayScopeValue {
  envelope: ScopeEnvelope;
  index: FieldIndex;
  /** 这个作用域里实际显示过、却没有译文的位置（供 BizNotice 统计）。 */
  missing: MissingRegistry;
}

/** 一个很小的外部存储：记录没有译文的位置，变化时通知 BizNotice。 */
export class MissingRegistry {
  private readonly map = new Map<string, string>();
  private readonly listeners = new Set<() => void>();
  private snapshot = 0;
  register(pointer: string, reason: string): void {
    if (this.map.get(pointer) === reason) return;
    this.map.set(pointer, reason);
    this.bump();
  }
  unregister(pointer: string): void {
    if (!this.map.delete(pointer)) return;
    this.bump();
  }
  count(): number {
    return this.snapshot;
  }
  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  private bump(): void {
    this.snapshot = this.map.size;
    for (const fn of this.listeners) fn();
  }
}

const ScopeContext = createContext<readonly DisplayScopeValue[]>([]);

export function envelopeOf(env: DisplayEnvelope): ScopeEnvelope {
  return {
    source: env.source,
    fields: env.fields ?? [],
    status: env.status,
    resource_version: env.resource_version ?? null,
    acknowledgements: env.acknowledgements ?? [],
    answer_states: env.answer_states ?? [],
  };
}

/** 即时回复（预览、服务错误）的附层：按当前语言取那一组字段，业务对象就是这份回复本身。 */
export function envelopeOfAttachment(source: unknown, att: DisplayAttachment | null | undefined, locale: "zh" | "en"): ScopeEnvelope | null {
  if (!att) return null;
  const tr = att.translations[locale];
  if (!tr) return null;
  return {
    source,
    fields: tr.fields ?? [],
    status: tr.status,
    resource_version: att.resource_version ?? null,
    acknowledgements: [],
    answer_states: [],
  };
}

export function buildScope(envelope: ScopeEnvelope): DisplayScopeValue {
  return { envelope, index: indexFields(envelope.fields), missing: new MissingRegistry() };
}

/** 把一个显示信封放进作用域（null / undefined = 还没有读到，子树照旧按外层或原文显示）。 */
export function DisplayScope({ envelope, children }: { envelope: ScopeEnvelope | DisplayEnvelope | null | undefined; children: ReactNode }) {
  const outer = useContext(ScopeContext);
  const stack = useMemo(() => {
    if (!envelope) return outer;
    const env: ScopeEnvelope = "display_schema_version" in envelope ? envelopeOf(envelope) : envelope;
    return [buildScope(env), ...outer];
  }, [envelope, outer]);
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
  /** 原文显示时的原因：服务报的缺译原因、no_field（这个位置没有字段）、null（没有作用域的 source 在这个位置有这段原文）。 */
  reason: DisplayField["missing_reason"] | "no_field" | null;
  /** 命中的作用域（统计缺译用）。 */
  scope: DisplayScopeValue | null;
}

/**
 * 按位置找一段原文的译文：由内向外，第一个在这个位置有同样原文的作用域说了算。
 * 有字段且 provided → 译文；有字段但 missing → 原文 + 服务的原因；source 有原文而没有字段 → 原文 + no_field；都没有 → 原文。
 */
export function lookupText(stack: readonly DisplayScopeValue[], pointer: string, original: string): LookedUp {
  for (const s of stack) {
    const at = pointerGet(s.envelope.source, pointer);
    if (at !== original) continue;
    const f = s.index.get(pointer);
    if (!f) return { text: original, provided: false, reason: "no_field", scope: s };
    if (f.status === "provided" && typeof f.text === "string" && f.text !== "") return { text: f.text, provided: true, reason: null, scope: s };
    return { text: original, provided: false, reason: f.missing_reason ?? "translation_missing", scope: s };
  }
  return { text: original, provided: false, reason: null, scope: null };
}

export function useLookup(pointer: string, original: string): LookedUp {
  const stack = useDisplayScopes();
  return lookupText(stack, pointer, original);
}

/** 原生控件（option、aria-label、title）要纯字符串：英文有就用英文，没有就原文。中文界面直接原文。 */
export function useText(pointer: string, original: string, locale: "zh" | "en"): string {
  const r = useLookup(pointer, original);
  return locale === "en" ? r.text : original;
}

/** 登记一次“实际显示了原文”（有作用域时记在命中的那个作用域上，让整块说明按真正显示的字段数写）。 */
export function useRegisterMissing(r: LookedUp, pointer: string, active: boolean): void {
  const scope = r.scope;
  const reason = r.reason;
  useEffect(() => {
    if (!active || !scope || r.provided) return;
    scope.missing.register(pointer, reason ?? "no_field");
    return () => scope.missing.unregister(pointer);
  }, [active, scope, pointer, reason, r.provided]);
}

/** 最内层作用域里实际显示过的缺译数（null = 没有作用域）。 */
export function useScopeMissingCount(): { missing: number; status: DisplayEnvelope["status"] } | null {
  const stack = useDisplayScopes();
  const s = stack[0];
  const subscribe = (fn: () => void) => (s ? s.missing.subscribe(fn) : () => undefined);
  const missing = useSyncExternalStore(subscribe, () => (s ? s.missing.count() : 0));
  if (!s) return null;
  return { missing, status: s.envelope.status };
}

/** 知悉项的说明：按 ack_id 在作用域里找（只有审阅的信封有）。 */
export function findAcknowledgement(stack: readonly DisplayScopeValue[], ackId: string): DisplayAcknowledgement | null {
  for (const s of stack) {
    const a = s.envelope.acknowledgements.find((x) => x.ack_id === ackId);
    if (a) return a;
  }
  return null;
}

/** 作答状态的名称与含义：按 (answer, state) 找，不按顺序。 */
export function findAnswerState(stack: readonly DisplayScopeValue[], answer: DisplayAnswerState["answer"], state: string): DisplayAnswerState | null {
  for (const s of stack) {
    const a = s.envelope.answer_states.find((x) => x.answer === answer && x.state === state);
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
  if (locale === "zh") return zh ? { text: zh, provided: true, reason: null, scope: null } : null;
  if (t.status === "provided" && t.en) return { text: t.en, provided: true, reason: null, scope: null };
  if (!zh) return null;
  return { text: zh, provided: false, reason: t.missing_reason ?? "translation_missing", scope: null };
}

/** 数组字段的位置：`${base}/${i}`。 */
export function at(base: string, i: number, ...rest: string[]): string {
  return [base, String(i), ...rest].join("/");
}
