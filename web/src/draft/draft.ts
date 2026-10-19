// 草稿就是共享结构里的 UserInputDraft：这里只放构造空草稿与读取的小工具，不另造一套表单值。
import type { AllocationGroup, ProductCapabilities, ProductMeta, UserInputDraft } from "../contract/types";

/** 草稿的 meta 照抄能力清单的 meta，去掉样例出处标签（真实消息不写 sample）。 */
export function metaFromCapabilities(caps: ProductCapabilities): ProductMeta {
  const { sample: _sample, ...rest } = caps.meta;
  void _sample;
  return structuredClone(rest);
}

function randomHex(bytes: number): string {
  const a = new Uint8Array(bytes);
  crypto.getRandomValues(a);
  return [...a].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** 本标签页的草稿编号（只生成一次）。格式符合 schema 的 RefStr。 */
export function newDraftId(): string {
  return `draft.web.${randomHex(8)}`;
}

/** 一份预检查的幂等键：idem- 加 32 位随机十六进制（长度 37，在 schema 允许的 8–80 之间）。 */
export function newIdempotencyKey(): string {
  return `idem-${randomHex(16)}`;
}

export function emptyDraft(caps: ProductCapabilities, draftId: string): UserInputDraft {
  return {
    meta: metaFromCapabilities(caps),
    draft_id: draftId,
    revision: 1,
    input_channel: "form",
    cities: { state: "unanswered", city_refs: [] },
    occupation: { state: "unanswered", raw_text: null, language: "zh", selected_candidate: null },
    age: { state: "unanswered", value: null },
    cash: { state: "unanswered", amount_text: null, currency: null, price_period: null, meaning: "liquid_funds_v1" },
    six_dimensions: { mode: null, entries: {} },
    material: { mode: null, entries: {} },
    health: { questionnaire_version: caps.health_questionnaire.version, items: [] },
    free_text: { state: "unanswered", text: null },
    attribute_preferences: [],
    abilities: [],
    hard_requirements: [],
    evidence: [],
  };
}

export function groupKeys(caps: ProductCapabilities, group: AllocationGroup): string[] {
  return group === "six_dimensions" ? caps.dimensions.map((d) => d.key) : caps.material_leaves.map((m) => m.key);
}

export function groupLabels(caps: ProductCapabilities, group: AllocationGroup): Record<string, string> {
  return Object.fromEntries(
    (group === "six_dimensions" ? caps.dimensions : caps.material_leaves).map((d) => [d.key, d.label]),
  );
}

export function equalValue(caps: ProductCapabilities, group: AllocationGroup): string {
  return group === "six_dimensions" ? caps.points.equal_split_six : caps.points.equal_split_material;
}

/** 两份草稿的内容是否相同（不比较 meta.sample；用来判断“载入的样例答案有没有被改过”）。 */
export function sameDraftContent(a: UserInputDraft, b: UserInputDraft): boolean {
  const strip = (d: UserInputDraft) => {
    const { sample: _s, ...meta } = d.meta;
    void _s;
    return { ...d, meta };
  };
  return stableStringify(strip(a)) === stableStringify(strip(b));
}

export function stableStringify(value: unknown): string {
  return JSON.stringify(sortDeep(value));
}

function sortDeep(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortDeep);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value as Record<string, unknown>)
        .sort()
        .filter((k) => (value as Record<string, unknown>)[k] !== undefined)
        .map((k) => [k, sortDeep((value as Record<string, unknown>)[k])]),
    );
  }
  return value;
}
