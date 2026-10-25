// 草稿的小工具。草稿就是共享结构里的 UserInputDraft，编号、修订号与 meta 由服务器给；这里不构造草稿，不另造一套表单值。
import type { AllocationGroup, ProductCapabilities, UserInputDraft } from "../contract/types";

export function groupKeys(caps: ProductCapabilities, group: AllocationGroup): string[] {
  return group === "six_dimensions" ? caps.dimensions.map((d) => d.key) : caps.material_leaves.map((m) => m.key);
}

export function groupLabels(caps: ProductCapabilities, group: AllocationGroup): Record<string, string> {
  return Object.fromEntries((group === "six_dimensions" ? caps.dimensions : caps.material_leaves).map((d) => [d.key, d.label]));
}

export function equalValue(caps: ProductCapabilities, group: AllocationGroup): string {
  return group === "six_dimensions" ? caps.points.equal_split_six : caps.points.equal_split_material;
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

/** 草稿的内容（不看修订号）：判断“有没有没保存的改动”、两份是不是同样的内容。 */
export function contentKey(d: UserInputDraft): string {
  const { revision: _r, ...rest } = d;
  void _r;
  return stableStringify(rest);
}

function randomHex(bytes: number): string {
  const a = new Uint8Array(bytes);
  crypto.getRandomValues(a);
  return [...a].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** 一次“确认开始”的幂等键：idem- 加 32 位随机十六进制（长度 37，在服务允许的 8–80 位之间）。 */
export function newIdempotencyKey(): string {
  return `idem-${randomHex(16)}`;
}
