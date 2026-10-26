// 业务对象里各段文字的位置（JSON Pointer）。显示作用域按位置取译文，并核对信封的 source 在同一位置就是这段原文，
// 所以页面要说清“这段字在哪个对象的哪个位置”：这里集中写能力清单（含输入目录）与运行列表里按编号找条目的几种位置，
// 结果、审阅、运行意图等对象的位置直接在页面里按数组下标拼（display/scope 的 at()）。找不到条目时返回一个不存在的位置，
// 作用域查不到就照原文显示（不猜）。
import type { InputCatalog, ProductCapabilities } from "../contract/types";
import { at } from "./scope";

export const MISSING_POINTER = "/__not_in_source__";

export function indexPointer<T>(list: readonly T[] | null | undefined, pred: (x: T) => boolean, base: string, ...rest: string[]): string {
  const i = (list ?? []).findIndex(pred);
  return i < 0 ? MISSING_POINTER : at(base, i, ...rest);
}

type InterestField = "leisure_interest" | "taste";
const panelKey = (field: InterestField) => (field === "taste" ? "taste" : "leisure");

/** 能力清单（GET /capabilities，信封 kind = capabilities）里的位置。 */
export const capsPtr = {
  city: (caps: ProductCapabilities | null | undefined, ref: string, field: "label" | "note" = "label") =>
    indexPointer(caps?.cities, (c) => c.city_ref === ref, "/cities", field),
  dimension: (caps: ProductCapabilities, key: string, field = "label") => indexPointer(caps.dimensions, (d) => d.key === key, "/dimensions", field),
  materialLeaf: (caps: ProductCapabilities, key: string, field = "label") => indexPointer(caps.material_leaves, (d) => d.key === key, "/material_leaves", field),
  occupation: (catalog: InputCatalog | null | undefined, conceptId: string, field = "label") =>
    indexPointer(catalog?.occupations, (o) => o.concept_id === conceptId, "/input_catalog/occupations", field),
  duty: (catalog: InputCatalog | null | undefined, conceptId: string, j: number) =>
    indexPointer(catalog?.occupations, (o) => o.concept_id === conceptId, "/input_catalog/occupations", "duties", String(j)),
  group: (catalog: InputCatalog | null | undefined, groupId: string) => indexPointer(catalog?.groups, (g) => g.group_id === groupId, "/input_catalog/groups", "label"),
  education: (catalog: InputCatalog | null | undefined, optionId: string) =>
    indexPointer(catalog?.education, (o) => o.option_id === optionId, "/input_catalog/education", "label"),
  certificate: (catalog: InputCatalog | null | undefined, abilityId: string) =>
    indexPointer(catalog?.certificates, (c) => c.ability_id === abilityId, "/input_catalog/certificates", "label"),
  experiencePrompt: (catalog: InputCatalog | null | undefined, questionId: string) =>
    indexPointer(catalog?.experience, (q) => q.question_id === questionId, "/input_catalog/experience", "prompt"),
  noMatch: "/input_catalog/search/no_match",
  panelNote: (field: InterestField) => `/input_catalog/${panelKey(field)}/note`,
  panelEntry: (catalog: InputCatalog | null | undefined, field: InterestField, key: string) =>
    indexPointer(catalog?.[panelKey(field)]?.entries, (e) => e.key === key, `/input_catalog/${panelKey(field)}/entries`, "label"),
  panelGroup: (catalog: InputCatalog | null | undefined, field: InterestField, groupId: string) =>
    indexPointer(catalog?.[panelKey(field)]?.groups, (g) => g.group_id === groupId, `/input_catalog/${panelKey(field)}/groups`, "label"),
  panelSearchNote: (catalog: InputCatalog | null | undefined, field: InterestField, noteId: string) =>
    indexPointer(catalog?.[panelKey(field)]?.search_notes, (n) => n.note_id === noteId, `/input_catalog/${panelKey(field)}/search_notes`, "note"),
  vocab: (caps: ProductCapabilities, field: InterestField, key: string) =>
    indexPointer(
      field === "taste" ? caps.interests.cuisines : caps.interests.leisure_categories,
      (v) => v.key === key,
      field === "taste" ? "/interests/cuisines" : "/interests/leisure_categories",
      "label",
    ),
  ability: (caps: ProductCapabilities, abilityId: string, field: "label" | "ask_when" = "label") =>
    indexPointer(caps.abilities, (a) => a.ability_id === abilityId, "/abilities", field),
  healthQuestion: (caps: ProductCapabilities, questionId: string) =>
    indexPointer(caps.health_questionnaire.questions, (q) => q.question_id === questionId, "/health_questionnaire/questions", "text"),
  healthOption: (caps: ProductCapabilities, optionId: string) =>
    indexPointer(caps.health_questionnaire.options, (o) => o.option_id === optionId, "/health_questionnaire/options", "label"),
  moneyBasis: (i: number) => at("/money_bases", i, "note"),
};

/** 结果对象（GET /runs/{id}/result，信封 kind = result）里按城市编号找的位置。 */
export const resultPtr = {
  city: (cities: readonly { city_ref: string }[] | null | undefined, ref: string, ...rest: string[]) =>
    indexPointer(cities, (c) => c.city_ref === ref, "/cities", ...(rest.length ? rest : ["label"])),
};
