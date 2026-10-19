// E3 问题卡里的 field（字段位置）对应到第几步，用来做“去修改”。对应不上的返回 null，放在页面顶部显示。
const PREFIX_TO_STEP: [string, number][] = [
  ["cities", 1],
  ["occupation", 1],
  ["age", 1],
  ["cash", 1],
  ["abilities", 1],
  ["six_dimensions", 2],
  ["material", 2],
  ["health", 3],
  ["free_text", 3],
  ["hard_requirements", 3],
  ["attribute_preferences", 3],
];

export function stepForField(field: string | undefined): number | null {
  if (!field) return null;
  for (const [prefix, step] of PREFIX_TO_STEP) {
    if (field === prefix || field.startsWith(`${prefix}.`)) return step;
  }
  return null;
}
