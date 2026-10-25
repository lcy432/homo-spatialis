// 输入目录（capabilities.input_catalog）的浏览与搜索：只按 E3 的规则筛已发布的条目，不生成新条目、不改答案。
// 搜索规则 catalog_search_nfkc_casefold_all_terms_v1（参考实现 product.input_catalog）：
//   查询与搜索词都做 Unicode NFKC（全角半角统一）、不分大小写、去首尾空白；按空白切词，每个词都要出现在该条目的某个搜索词里；
//   空查询给出（当前分组的）全部条目；结果按目录顺序。
import type { CatalogOccupation, InputCatalog, InterestEntry, InterestPanel, SearchNote } from "../contract/types";

export function normalizeTerm(s: string): string {
  return s.normalize("NFKC").toLowerCase().trim();
}

export function queryWords(query: string): string[] {
  return normalizeTerm(query).split(/\s+/).filter(Boolean);
}

/** 每个词都要在某个搜索词里出现（子串）；没有词 = 命中。 */
export function termsMatch(query: string, terms: readonly string[]): boolean {
  const words = queryWords(query);
  if (!words.length) return true;
  const norm = terms.map(normalizeTerm);
  return words.every((w) => norm.some((t) => t.includes(w)));
}

export function searchOccupations(catalog: InputCatalog, query: string, groupId: string | null): CatalogOccupation[] {
  return catalog.occupations
    .filter((o) => (groupId === null || o.group_id === groupId) && termsMatch(query, o.search_terms))
    .sort((a, b) => a.order - b.order);
}

export function searchPanel(
  panel: InterestPanel,
  query: string,
  groupId: string | null,
): { entries: InterestEntry[]; notes: SearchNote[] } {
  const entries = panel.entries
    .filter((e) => (groupId === null || e.group_id === groupId) && termsMatch(query, e.search_terms))
    .sort((a, b) => a.order - b.order);
  // 搜索说明只在有查询词时显示（例如“川菜”→ 中餐 + 本版不区分地方菜；“徒步”→ 本版没有这个类别）
  const notes = queryWords(query).length ? (panel.search_notes ?? []).filter((n) => termsMatch(query, n.terms)) : [];
  return { entries, notes };
}

export function occupationById(catalog: InputCatalog | null | undefined, conceptId: string | null | undefined): CatalogOccupation | null {
  if (!catalog || !conceptId) return null;
  return catalog.occupations.find((o) => o.concept_id === conceptId) ?? null;
}
