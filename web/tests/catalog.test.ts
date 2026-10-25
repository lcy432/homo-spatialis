// @vitest-environment node
// 目录搜索按 E3 的规则（catalog_search_nfkc_casefold_all_terms_v1），对照交接单第 14.4 节的表：结果只筛已发布的条目、不增加、不换职业。
import { describe, expect, it } from "vitest";
import { samplePayload } from "../src/contract/samples";
import type { InputCatalog, ProductCapabilities } from "../src/contract/types";
import { searchOccupations, searchPanel, termsMatch } from "../src/draft/catalog";

const catalog = samplePayload<ProductCapabilities>("129").input_catalog as InputCatalog;
const labels = (q: string, g: string | null = null) => searchOccupations(catalog, q, g).map((o) => o.label);

describe("职业目录搜索", () => {
  it("程序员 / 全角大写 PROGRAMMER / Software Developers 都找到软件开发工程师", () => {
    for (const q of ["程序员", "ＰＲＯＧＲＡＭＭＥＲ", "Software Developers"]) expect(labels(q), q).toEqual(["软件开发工程师"]);
  });

  it("两个词都要命中：“软件 工程师”找到，“软件 厨师”没有；教师、护士没有", () => {
    expect(labels("软件 工程师")).toEqual(["软件开发工程师"]);
    expect(labels("软件 厨师")).toEqual([]);
    expect(labels("教师")).toEqual([]);
    expect(labels("护士")).toEqual([]);
  });

  it("空查询 + 分组 it_tech：固定顺序的四个职业；全角空格也算空白", () => {
    expect(labels("", "it_tech")).toEqual(["电脑用户支持专员", "网络支持专员", "软件开发工程师", "计算机系统分析师"]);
    expect(labels("软件　工程师")).toEqual(["软件开发工程师"]);
    expect(labels("").length).toBe(catalog.occupations.length);
  });

  it("纯函数：不改目录，不生成条目", () => {
    const before = JSON.stringify(catalog);
    labels("文员");
    expect(JSON.stringify(catalog)).toBe(before);
    expect(termsMatch("  ", ["x"])).toBe(true);
  });
});

describe("兴趣与菜系面板搜索", () => {
  it("川菜 → 中餐 + 说明 taste.chinese_regional（不单独记成川菜）", () => {
    const r = searchPanel(catalog.taste, "川菜", null);
    expect(r.entries.map((e) => e.key)).toEqual(["chinese"]);
    expect(r.notes.map((n) => n.note_id)).toEqual(["taste.chinese_regional"]);
  });

  it("徒步 / 打游戏：没有类别，只有说明；跑步 → 健身", () => {
    expect(searchPanel(catalog.leisure, "徒步", null).entries).toEqual([]);
    expect(searchPanel(catalog.leisure, "徒步", null).notes.length).toBeGreaterThan(0);
    expect(searchPanel(catalog.leisure, "打游戏", null).entries).toEqual([]);
    expect(searchPanel(catalog.leisure, "跑步", null).entries.map((e) => e.key)).toEqual(["fitness"]);
    expect(searchPanel(catalog.leisure, "", null).notes).toEqual([]);
  });
});
