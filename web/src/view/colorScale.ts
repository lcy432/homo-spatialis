// 地图与比较图共用的 J 色阶（所有者 2026-10-24 决定）：城市之间差距很小时适度放大，但不放大成“第一名满分、最后一名零分”。
//
// 窗口：本轮所有城市（所有国家共用同一个尺度，不按国家分开）的平均 J 里，最低 a、最高 b。
//   宽度 w = min(1, max(MIN_WINDOW, 2 × (b − a)))，以 (a + b) / 2 为中心；超出 [0, 1] 就整体平移回来。
//   于是最高、最低的城市落在色阶的 25% 与 75%（差距本来就大、窗口顶到 [0, 1] 时更靠两端）；差距小于 0.01 时窗口固定宽 0.02：
//   两城差 0.004 落在 40% 与 60%（看得出谁高谁低，又不画成两个极端）；差 0.0002 只差 1%，颜色相同，表示“几乎一样”。
// 手算（最新六城样例 104，离线 mock）：a = 0.5141（休斯敦），b = 0.5881（芝加哥），w = 0.148，窗口 [0.4771, 0.6251]；
//   休斯敦 (0.5141 − 0.4771) / 0.148 = 25%，芝加哥 75%。
// 窗口只决定颜色的深浅；数值、范围与比较结论照公共值显示，图例写明两端不代表 0 分或满分。没有均值的城市（C = 0、未生成）不上色。
export const MIN_WINDOW = 0.02;

export interface ColorWindow {
  lo: number;
  hi: number;
}

export function colorWindow(values: readonly number[]): ColorWindow | null {
  const vs = values.filter((v) => Number.isFinite(v));
  if (!vs.length) return null;
  const a = Math.min(...vs);
  const b = Math.max(...vs);
  const w = Math.min(1, Math.max(MIN_WINDOW, 2 * (b - a)));
  let lo = (a + b) / 2 - w / 2;
  let hi = lo + w;
  if (lo < 0) {
    hi -= lo;
    lo = 0;
  }
  if (hi > 1) {
    lo -= hi - 1;
    hi = 1;
  }
  return { lo: Math.max(0, lo), hi: Math.min(1, hi) };
}

/** 一个值在窗口里的位置（0–1，超出窗口的值夹在两端只影响颜色，不影响数值显示）。 */
export function positionIn(v: number, w: ColorWindow): number {
  if (w.hi <= w.lo) return 0.5;
  return Math.min(1, Math.max(0, (v - w.lo) / (w.hi - w.lo)));
}

// ---- 颜色：在 OKLab 里线性插值（感知上均匀），低 → 高。两种外观各一对端点；都避开红绿二分。
type RGB = [number, number, number];

function hexToRgb(hex: string): RGB {
  const n = Number.parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function toLinear(c: number): number {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function fromLinear(c: number): number {
  const s = c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
  return Math.round(Math.min(1, Math.max(0, s)) * 255);
}

function rgbToOklab([r, g, b]: RGB): RGB {
  const lr = toLinear(r);
  const lg = toLinear(g);
  const lb = toLinear(b);
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

function oklabToRgb([L, a, b]: RGB): RGB {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    fromLinear(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    fromLinear(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    fromLinear(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  ];
}

export const RAMPS = {
  paper: { low: "#e6d6ae", high: "#1d4f7a" },
  globe: { low: "#33506a", high: "#ffd36e" },
} as const;

export function rampColor(t: number, mode: keyof typeof RAMPS): string {
  const { low, high } = RAMPS[mode];
  const a = rgbToOklab(hexToRgb(low));
  const b = rgbToOklab(hexToRgb(high));
  const x = Math.min(1, Math.max(0, t));
  const mix: RGB = [a[0] + (b[0] - a[0]) * x, a[1] + (b[1] - a[1]) * x, a[2] + (b[2] - a[2]) * x];
  const [r, g, bl] = oklabToRgb(mix);
  return `#${[r, g, bl].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

/** 窗口里几个刻度值（图例用）。 */
export function windowTicks(w: ColorWindow, n = 5): number[] {
  return Array.from({ length: n }, (_, i) => w.lo + ((w.hi - w.lo) * i) / (n - 1));
}
