import { describe, expect, it } from "vitest";
import { Fraction, canonical, displayFixed, formatComputed, parsePoints, remainingOf, sumProvided } from "../src/draft/exact";

describe("精确分数（填写辅助）", () => {
  it("与 E3 相同的写法规则：十进制最多两位小数、约分后的分数", () => {
    expect(parsePoints("30", 2)).toMatchObject({ ok: true });
    expect(parsePoints(" 16.5 ", 2)).toMatchObject({ ok: true });
    expect(parsePoints("50/3", 2)).toMatchObject({ ok: true });
    expect(parsePoints("16.555", 2)).toMatchObject({ ok: false, reason: "too_many_decimals" });
    expect(parsePoints("100/6", 2)).toMatchObject({ ok: false, reason: "fraction_not_canonical" });
    expect(parsePoints("05", 2)).toMatchObject({ ok: false, reason: "not_a_number" });
    expect(parsePoints("-5", 2)).toMatchObject({ ok: false, reason: "not_a_number" });
    expect(parsePoints("1e2", 2)).toMatchObject({ ok: false, reason: "not_a_number" });
  });

  it("均分后把安全改成 20：精确多出 10/3（显示 3.3），用显示值 16.7 去算会错成 3.5", () => {
    const entries = {
      material: { state: "provided", value_text: "50/3" },
      safety: { state: "provided", value_text: "20" },
      opportunity: { state: "provided", value_text: "50/3" },
      leisure: { state: "provided", value_text: "50/3" },
      environment: { state: "provided", value_text: "50/3" },
      commute: { state: "provided", value_text: "50/3" },
    };
    const keys = Object.keys(entries);
    const { total } = sumProvided(entries, keys, 2);
    expect(canonical(total)).toBe("310/3");
    const r = remainingOf(total);
    expect(r.kind).toBe("over");
    expect(canonical(r.amount)).toBe("10/3");
    expect(displayFixed(r.amount, 1)).toBe("3.3");
    // 错误的做法：拿显示值相加
    const wrong = new Fraction("16.7").mul(5).add(20).sub(100);
    expect(displayFixed(wrong, 1)).toBe("3.5");
  });

  it("显示四舍五入（半数进位），与 E3 的 ROUND_HALF_UP 一致", () => {
    expect(displayFixed(new Fraction("50/3"), 1)).toBe("16.7");
    expect(displayFixed(new Fraction("100/3"), 1)).toBe("33.3");
    expect(displayFixed(new Fraction("0.25"), 1)).toBe("0.3");
    expect(displayFixed(new Fraction("0.05"), 1)).toBe("0.1");
    expect(displayFixed(new Fraction("100"), 1)).toBe("100.0");
    expect(displayFixed(new Fraction("0"), 1)).toBe("0.0");
  });

  it("算出来的值：能写成最多两位小数就写小数，否则写规范分数", () => {
    expect(formatComputed(new Fraction("16.5").add(5), 2)).toBe("21.5");
    expect(formatComputed(new Fraction("50/3").add(5), 2)).toBe("65/3");
    expect(formatComputed(new Fraction("50/3").sub("10/3"), 2)).toBe("40/3");
    expect(formatComputed(new Fraction(0), 2)).toBe("0");
    expect(formatComputed(new Fraction(25), 2)).toBe("25");
    expect(formatComputed(new Fraction("0.05"), 2)).toBe("0.05");
  });

  it("写法不对的项不计入合计，也单独列出", () => {
    const { total, invalid, counted } = sumProvided(
      { a: { state: "provided", value_text: "30" }, b: { state: "provided", value_text: "三十" }, c: { state: "unsure" } },
      ["a", "b", "c"],
      2,
    );
    expect(canonical(total)).toBe("30");
    expect(invalid).toEqual(["b"]);
    expect(counted).toEqual(["a"]);
  });
});
