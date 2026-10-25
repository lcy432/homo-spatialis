// E3 问题卡里的 field（字段位置）对应到哪一步，用来做“去修改”。对应不上的返回 null（问题照常显示在审阅页）。
export const STEP_SLUGS = ["place", "facts", "priorities", "more", "review"] as const;
export type StepSlug = (typeof STEP_SLUGS)[number];

const PREFIX_TO_STEP: [string, StepSlug][] = [
  ["cities", "place"],
  ["occupation", "facts"],
  ["age", "facts"],
  ["cash", "facts"],
  ["abilities", "facts"],
  ["education", "facts"],
  ["certificates", "facts"],
  ["experience", "facts"],
  ["six_dimensions", "priorities"],
  ["material", "priorities"],
  ["meta.vocabulary_version", "priorities"],
  ["meta.questionnaire_version", "priorities"],
  ["health", "more"],
  ["meta.health_questionnaire_version", "more"],
  ["leisure_interest", "more"],
  ["taste", "more"],
  ["free_text", "more"],
  ["hard_requirements", "more"],
  ["attribute_preferences", "more"],
];

export function stepForField(field: string | null | undefined): StepSlug | null {
  if (!field) return null;
  for (const [prefix, step] of PREFIX_TO_STEP) {
    if (field === prefix || field.startsWith(`${prefix}.`)) return step;
  }
  return null;
}

export function stepIndex(step: StepSlug): number {
  return STEP_SLUGS.indexOf(step) + 1;
}
