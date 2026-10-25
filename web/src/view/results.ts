// 结果的纯展示模型：只组织公共事实，不算新的分数、缺失范围、资格或排名。城市顺序照服务给的顺序（不按分数重排）。
// 精确值（exact）只用来定位图上的点与颜色；文字一律显示 E3 给的 display。
import Fraction from "fraction.js";
import type { CityResult, ExactNumber, ProductCapabilities, ProductResult } from "../contract/types";

export function exactToNumber(x: ExactNumber | null | undefined): number | null {
  if (!x) return null;
  try {
    return new Fraction(x.exact).valueOf();
  } catch {
    return null;
  }
}

export interface CityRow {
  cityRef: string;
  label: string;
  country: string | null;
  city: CityResult;
  mean: number | null;
  low: number | null;
  high: number | null;
}

export function cityRows(result: ProductResult, caps: ProductCapabilities | null): CityRow[] {
  return (result.cities ?? []).map((c) => ({
    cityRef: c.city_ref,
    label: c.label,
    country: caps?.cities.find((o) => o.city_ref === c.city_ref)?.country ?? countryFromRef(c.city_ref),
    city: c,
    mean: exactToNumber(c.mean_j),
    low: exactToNumber(c.range_lower),
    high: exactToNumber(c.range_upper),
  }));
}

/** city_ref 的第一段是国家（“US:CBSA2023:42660”）；能力清单里找不到这座城时才用它分组。 */
export function countryFromRef(ref: string): string | null {
  const head = ref.split(":")[0];
  return head && head.length >= 2 ? head : null;
}

export function groupByCountry(rows: readonly CityRow[]): { country: string; rows: CityRow[] }[] {
  const out: { country: string; rows: CityRow[] }[] = [];
  for (const r of rows) {
    const key = r.country ?? "—";
    let g = out.find((x) => x.country === key);
    if (!g) {
      g = { country: key, rows: [] };
      out.push(g);
    }
    g.rows.push(r);
  }
  return out;
}

/** 比较图的数轴：包含全部均值与全部范围端点（不裁掉任何一个），两端留一点空。 */
export function chartDomain(rows: readonly CityRow[]): [number, number] | null {
  const vals: number[] = [];
  for (const r of rows) for (const v of [r.mean, r.low, r.high]) if (v !== null) vals.push(v);
  if (!vals.length) return null;
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  const pad = Math.max(0.01, (hi - lo) * 0.08);
  lo = Math.max(0, lo - pad);
  hi = Math.min(1, hi + pad);
  if (hi - lo < 0.02) {
    const mid = (hi + lo) / 2;
    lo = Math.max(0, mid - 0.01);
    hi = Math.min(1, mid + 0.01);
  }
  return [lo, hi];
}

export function niceTicks([lo, hi]: [number, number], target = 5): number[] {
  const span = hi - lo;
  const raw = span / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? mag * 10;
  const start = Math.ceil(lo / step) * step;
  const out: number[] = [];
  for (let v = start; v <= hi + step * 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

/** 刻度标签统一的小数位：能把每个刻度准确写出的最少位数（步长 0.0025 时写四位，不会出现三个“0.56”）。 */
export function tickDecimals(ticks: readonly number[]): number {
  for (let d = 0; d < 6; d++) if (ticks.every((v) => Math.abs(Number(v.toFixed(d)) - v) < 1e-9)) return d;
  return 6;
}

export function cloneNumber(cloneRef: string, repeatIndex: number): string {
  return String(repeatIndex + 1 || cloneRef);
}
