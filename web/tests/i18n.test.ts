import { describe, expect, it } from "vitest";
import { en } from "../src/i18n/en";
import { zh } from "../src/i18n/zh";

type Tree = { [k: string]: string | Tree };

function leaves(t: Tree, prefix = ""): Map<string, string> {
  const out = new Map<string, string>();
  for (const [k, v] of Object.entries(t)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "string") out.set(key, v);
    else for (const [kk, vv] of leaves(v, key)) out.set(kk, vv);
  }
  return out;
}

const vars = (s: string) => [...s.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]).sort();

describe("界面字典", () => {
  const z = leaves(zh as unknown as Tree);
  const e = leaves(en as unknown as Tree);

  it("中英两部字典的键一一对应", () => {
    expect([...e.keys()].sort()).toEqual([...z.keys()].sort());
  });

  it("没有空文字；每个键的插值变量相同（不丢数字）", () => {
    for (const [k, v] of z) {
      expect(v.trim(), k).not.toBe("");
      expect((e.get(k) ?? "").trim(), k).not.toBe("");
      expect(vars(e.get(k) ?? ""), k).toEqual(vars(v));
    }
  });

  it("停机上限不写成“预计费用”", () => {
    expect(zh.prepare.maxCostHelp).toContain("不是预计费用");
    expect(en.prepare.maxCostHelp).toContain("not an expected cost");
  });
});
