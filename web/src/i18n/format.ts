// 日期与月份的显示格式。时间一律用服务给的时间戳（UTC），按浏览器所在时区显示；不从收到的时刻、查询次数推算任何期限。
// 金额与分数不在这里格式化：显示 E3 给的 display，精确值另附，界面不重新舍入。
import type { Locale } from "../prefs";

export function formatTime(iso: string | null | undefined, locale: Locale): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-US", { dateStyle: "medium", timeStyle: "short" }).format(d);
}

/** 月份列表 [1, 2, 3, 7] 写成 “1–3、7” / “1–3, 7”。 */
export function formatMonths(months: readonly number[] | null | undefined, locale: Locale): string {
  if (!months?.length) return "";
  const sorted = [...new Set(months)].sort((a, b) => a - b);
  const parts: string[] = [];
  let start = sorted[0] as number;
  let prev = start;
  for (const m of [...sorted.slice(1), Number.NaN]) {
    if (m === prev + 1) {
      prev = m;
      continue;
    }
    parts.push(start === prev ? String(start) : `${start}–${prev}`);
    start = m;
    prev = m;
  }
  return parts.join(locale === "zh" ? "、" : ", ");
}
