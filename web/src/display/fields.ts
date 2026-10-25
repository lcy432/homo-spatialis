// 显示附层（e3-display-v1）的读法。业务状态只从 source 读；fields 只提供某个 JSON Pointer 位置的文字：
//   provided → 用服务给的译文；missing → 显示 source 里的中文原文并标明缺译（不自己翻译，不拿中文去查别的字典）。
// 中文界面不用这一层：原文就在业务对象里。这里只是纯函数；缓存与请求见 api/queries.ts，页面接法见 i18n/biz.tsx。
import type { DisplayAttachment, DisplayEnvelope, DisplayField } from "../contract/types";

export type FieldIndex = ReadonlyMap<string, DisplayField>;

export function indexFields(fields: readonly DisplayField[] | null | undefined): FieldIndex {
  return new Map((fields ?? []).map((f) => [f.pointer, f] as const));
}

/** 即时回复里的附层（确认回复、错误）：取某种语言的那一组文字。 */
export function attachmentFields(att: DisplayAttachment | null | undefined, locale: "zh" | "en"): FieldIndex {
  return indexFields(att?.translations[locale]?.fields);
}

export function envelopeFields(env: DisplayEnvelope | null | undefined): FieldIndex {
  return indexFields(env?.fields);
}

/** RFC 6901：按 JSON Pointer 从 source 取值（只用来核对与取原文）。 */
export function pointerGet(obj: unknown, pointer: string): unknown {
  if (pointer === "") return obj;
  let cur: unknown = obj;
  for (const raw of pointer.split("/").slice(1)) {
    const key = raw.replace(/~1/g, "/").replace(/~0/g, "~");
    if (Array.isArray(cur)) cur = cur[Number(key)];
    else if (cur && typeof cur === "object") cur = (cur as Record<string, unknown>)[key];
    else return undefined;
  }
  return cur;
}

export interface ResolvedText {
  text: string;
  /** true = 服务给了这种语言的文字；false = 显示的是中文原文 */
  provided: boolean;
  missingReason: DisplayField["missing_reason"] | "no_field" | null;
}

/**
 * 某个位置该显示什么：有译文用译文；没有（字段缺、状态 missing）就是原文并说明原因。
 * 原文永远来自调用方手里的业务对象，不从 fields 里拿。
 */
export function resolveText(index: FieldIndex, pointer: string, original: string): ResolvedText {
  const f = index.get(pointer);
  if (!f) return { text: original, provided: false, missingReason: "no_field" };
  if (f.status === "provided" && typeof f.text === "string" && f.text !== "") return { text: f.text, provided: true, missingReason: null };
  return { text: original, provided: false, missingReason: f.missing_reason ?? null };
}

/** 覆盖情况：给了多少、缺多少（页面顶部一句话用，不是“英文完整”的证明）。 */
export function coverage(index: FieldIndex): { provided: number; missing: number } {
  let provided = 0;
  let missing = 0;
  for (const f of index.values()) {
    if (f.status === "provided") provided += 1;
    else missing += 1;
  }
  return { provided, missing };
}
