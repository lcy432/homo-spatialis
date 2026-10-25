import { describe, expect, it } from "vitest";
import { samplePayload } from "../src/contract/samples";
import type { ProductCapabilities, ProductResult } from "../src/contract/types";
import { chartDomain, cityRows, exactToNumber, groupByCountry, niceTicks, tickDecimals } from "../src/view/results";

const six = samplePayload<ProductResult>("104");

describe("结果的展示模型", () => {
  it("城市顺序照服务给的顺序，不按分数重排", () => {
    const rows = cityRows(six, null);
    expect(rows.map((r) => r.cityRef)).toEqual((six.cities ?? []).map((c) => c.city_ref));
  });

  it("精确值只用来定位：0.5683 这样的写法读成数", () => {
    expect(exactToNumber({ exact: "5683/10000", display: "0.5683" })).toBeCloseTo(0.5683, 10);
    expect(exactToNumber(null)).toBeNull();
  });

  it("数轴包含全部均值与范围端点，均值落在范围外时两者都不被裁掉", () => {
    // 合法的展示夹具：1 个完整分身 J = 0.3；1 个缺失分身已有的月份分数很高（下界 0.6、上界 0.8）→ 城市范围 [0.45, 0.55]
    const [lo, hi] = chartDomain([
      { cityRef: "x", label: "x", country: null, city: {} as never, mean: 0.3, low: 0.45, high: 0.55 },
    ])!;
    expect(lo).toBeLessThanOrEqual(0.3);
    expect(hi).toBeGreaterThanOrEqual(0.55);
  });

  it("按国家分组；能力清单里没有的城市用编号的国家段", () => {
    const caps = samplePayload<ProductCapabilities>("01");
    const groups = groupByCountry(cityRows(six, caps));
    expect(groups.map((g) => g.country)).toEqual(["US"]);
  });

  it("刻度落在数轴内", () => {
    const ticks = niceTicks([0.47, 0.63]);
    expect(ticks.length).toBeGreaterThan(2);
    expect(ticks.every((v) => v >= 0.47 - 1e-9 && v <= 0.63 + 1e-9)).toBe(true);
  });

  it("刻度标签各不相同：步长 0.0025 时写四位小数（不会出现三个“0.56”）", () => {
    const ticks = niceTicks([0.5502, 0.5743]);
    const labels = ticks.map((v) => v.toFixed(tickDecimals(ticks)));
    expect(new Set(labels).size).toBe(labels.length);
    expect(tickDecimals([0.55, 0.56, 0.57])).toBe(2);
    expect(tickDecimals([0.5575, 0.56])).toBe(4);
  });
});
