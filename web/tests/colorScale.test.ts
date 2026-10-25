import { describe, expect, it } from "vitest";
import { MIN_WINDOW, colorWindow, positionIn, rampColor } from "../src/view/colorScale";

describe("J 色阶：差距小时适度放大，不放大成满分与零分", () => {
  it("手算：六城样例 104 的最低 0.5141、最高 0.5881 → 窗口 [0.4771, 0.6251]，两端城市落在 25% 与 75%", () => {
    const w = colorWindow([0.5683, 0.5672, 0.5656, 0.5881, 0.5141, 0.5746]);
    expect(w?.lo).toBeCloseTo(0.4771, 4);
    expect(w?.hi).toBeCloseTo(0.6251, 4);
    expect(positionIn(0.5141, w!)).toBeCloseTo(0.25, 6);
    expect(positionIn(0.5881, w!)).toBeCloseTo(0.75, 6);
  });

  it("差距很小（0.004）：窗口固定宽 0.02，两城落在 40% 与 60%——看得出高低，不画成两个极端", () => {
    const w = colorWindow([0.5602, 0.5642])!;
    expect(w.hi - w.lo).toBeCloseTo(MIN_WINDOW, 10);
    expect(positionIn(0.5602, w)).toBeCloseTo(0.4, 6);
    expect(positionIn(0.5642, w)).toBeCloseTo(0.6, 6);
    expect(rampColor(0.4, "paper")).not.toBe(rampColor(0.6, "paper"));
  });

  it("差距小到 0.0002：只差 1%，颜色相同（几乎一样就画成一样）", () => {
    const w = colorWindow([0.5, 0.5002])!;
    expect(positionIn(0.5002, w) - positionIn(0.5, w)).toBeCloseTo(0.01, 6);
  });

  it("差距大（跨国）：窗口顶到 [0, 1] 时整体平移，不超出评分范围", () => {
    const w = colorWindow([0.1, 0.9])!;
    expect(w).toEqual({ lo: 0, hi: 1 });
    const edge = colorWindow([0.95, 0.99])!;
    expect(edge.hi).toBe(1);
    expect(edge.hi - edge.lo).toBeCloseTo(0.08, 10);
    expect(edge.lo).toBeCloseTo(0.92, 10);
  });

  it("只有一座城有均值：放在中间；没有任何均值：没有色阶（不上色）", () => {
    const w = colorWindow([0.42])!;
    expect(positionIn(0.42, w)).toBeCloseTo(0.5, 6);
    expect(colorWindow([])).toBeNull();
  });

  it("颜色两端就是定义的端点；中间按感知均匀插值", () => {
    expect(rampColor(0, "paper")).toBe("#e6d6ae");
    expect(rampColor(1, "paper")).toBe("#1d4f7a");
    expect(rampColor(0.5, "paper")).not.toBe(rampColor(0.4, "paper"));
  });
});
