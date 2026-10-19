// 把月份编号写成区间（[1..12] → “1–12 月”，[1,2,3,5] → “1–3、5 月”）。只是排版，不改任何数字。
export function monthsText(months: readonly number[] | undefined): string {
  if (!months?.length) return "没有";
  const sorted = [...new Set(months)].sort((a, b) => a - b);
  const parts: string[] = [];
  let start = sorted[0] as number;
  let prev = start;
  for (const m of sorted.slice(1)) {
    if (m === prev + 1) {
      prev = m;
      continue;
    }
    parts.push(start === prev ? `${start}` : `${start}–${prev}`);
    start = m;
    prev = m;
  }
  parts.push(start === prev ? `${start}` : `${start}–${prev}`);
  return `${parts.join("、")} 月`;
}
