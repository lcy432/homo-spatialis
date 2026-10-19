// 填写辅助用的精确分数（fraction.js，内部是 BigInt）。只用来算“还剩几分 / 多出几分”这类提示；正式合计以 E3 审阅为准。
//
// 能解析的写法与 E3 的 src/product/exact.py 一致：非负十进制（不以 0 开头，最多若干位小数）或约分后的规范分数（50/3）。
// 显示用四舍五入（半数进位），与 E3 相同；显示值只供阅读，不回流计算。
import Fraction from "fraction.js";

const DECIMAL = /^(0|[1-9][0-9]*)(\.[0-9]+)?$/;
const FRACTION = /^(0|[1-9][0-9]*)\/([1-9][0-9]*)$/;

export type PointsParse =
  | { ok: true; value: Fraction }
  | { ok: false; reason: "not_a_number" | "too_many_decimals" | "fraction_not_canonical"; hint: string };

export function parsePoints(text: string, maxDecimals: number): PointsParse {
  const s = text.trim();
  if (DECIMAL.test(s)) {
    const decimals = s.includes(".") ? (s.split(".")[1] ?? "").length : 0;
    if (decimals > maxDecimals) {
      return { ok: false, reason: "too_many_decimals", hint: `最多 ${maxDecimals} 位小数（步长不是测量精度）` };
    }
    return { ok: true, value: new Fraction(s) };
  }
  const m = FRACTION.exec(s);
  if (m) {
    const q = new Fraction(BigInt(m[1] as string), BigInt(m[2] as string));
    if (canonical(q) !== s) {
      return { ok: false, reason: "fraction_not_canonical", hint: `分数要写成约分后的样子，例如 ${canonical(q)}` };
    }
    return { ok: true, value: q };
  }
  return { ok: false, reason: "not_a_number", hint: "写成数字（例如 30、16.5）或约分后的分数（例如 50/3）" };
}

/** 规范写法：约分后的整数（"30"）或 分子/分母（"50/3"）。 */
export function canonical(q: Fraction): string {
  const n = q.s * q.n;
  return q.d === 1n ? n.toString() : `${n.toString()}/${q.d.toString()}`;
}

/** 四舍五入到 decimals 位小数（半数向远离 0 的方向进位），返回显示文字。 */
export function displayFixed(q: Fraction, decimals: number): string {
  const scale = 10n ** BigInt(decimals);
  const num = q.n * scale;
  let whole = num / q.d;
  const rem = num % q.d;
  if (rem * 2n >= q.d) whole += 1n;
  const sign = q.s < 0n && whole !== 0n ? "-" : "";
  const digits = whole.toString().padStart(decimals + 1, "0");
  if (decimals === 0) return `${sign}${digits}`;
  return `${sign}${digits.slice(0, -decimals)}.${digits.slice(-decimals)}`;
}

/**
 * 按钮或挪分算出来的新值怎样写进草稿：能写成最多 maxDecimals 位小数就写小数（16.5 + 5 → "21.5"），否则写规范分数（50/3 + 5 → "65/3"）。
 * 用户亲手输入的文字不经过这里，照原样保存。
 */
export function formatComputed(q: Fraction, maxDecimals: number): string {
  if (q.s < 0n) throw new Error("点数不能是负数");
  const scale = 10n ** BigInt(maxDecimals);
  if ((q.n * scale) % q.d !== 0n) return canonical(q);
  const scaled = (q.n * scale) / q.d;
  let text = scaled.toString().padStart(maxDecimals + 1, "0");
  if (maxDecimals > 0) {
    text = `${text.slice(0, -maxDecimals)}.${text.slice(-maxDecimals)}`.replace(/\.?0+$/, "");
  }
  return text === "" ? "0" : text;
}

export interface PointsTotal {
  total: Fraction;
  /** 已提供而且能解析的项。 */
  counted: string[];
  /** 已提供但写法不对的项（不计入合计，也不改写）。 */
  invalid: string[];
}

export function sumProvided(
  entries: Record<string, { state: string; value_text?: string | null } | undefined>,
  keys: readonly string[],
  maxDecimals: number,
): PointsTotal {
  let total = new Fraction(0);
  const counted: string[] = [];
  const invalid: string[] = [];
  for (const k of keys) {
    const e = entries[k];
    if (!e || e.state !== "provided" || e.value_text == null) continue;
    const p = parsePoints(e.value_text, maxDecimals);
    if (p.ok) {
      total = total.add(p.value);
      counted.push(k);
    } else {
      invalid.push(k);
    }
  }
  return { total, counted, invalid };
}

export type Remaining =
  | { kind: "remaining"; amount: Fraction }
  | { kind: "full"; amount: Fraction }
  | { kind: "over"; amount: Fraction };

export function remainingOf(total: Fraction, target = 100): Remaining {
  const diff = new Fraction(target).sub(total);
  if (diff.s === 0n || diff.n === 0n) return { kind: "full", amount: new Fraction(0) };
  if (diff.s > 0n) return { kind: "remaining", amount: diff };
  return { kind: "over", amount: diff.neg() };
}

export { Fraction };
