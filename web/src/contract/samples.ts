// E3 的公共样例（contracts/product/v3/samples/）。每份是 {message, description_zh, payload}；这里只按文件编号取用，原样返回。
// 只给开发用的样例画廊、组件测试与草稿样本生成用；正常产品入口一律走 HTTP，不会退回到样例。
// 样例全部是合成人物与 mock 规划器的结果（51–55、104 用冻结的真实资料包）；带 contract_example 标签的是标明为示例的契约响应。
// 目录用别名 @contracts（见 vite.config.ts）。
import type { ValidatedName } from "./types";

export interface SampleFile {
  /** 文件编号，例如 "02"、"27b"。 */
  id: string;
  file: string;
  message: ValidatedName;
  description_zh: string;
  payload: unknown;
}

const raw = import.meta.glob<{ message: string; description_zh: string; payload: unknown }>(
  "@contracts/samples/*.json",
  { eager: true, import: "default" },
);

const samples = new Map<string, SampleFile>();
for (const [p, s] of Object.entries(raw)) {
  const file = p.split("/").pop() as string;
  const id = file.split("_")[0] as string;
  samples.set(id, { id, file, message: s.message as ValidatedName, description_zh: s.description_zh, payload: s.payload });
}

export const SAMPLE_IDS = [...samples.keys()].sort();

export function sample(id: string): SampleFile {
  const s = samples.get(id);
  if (!s) throw new Error(`没有编号为 ${id} 的公共样例`);
  return s;
}

/** 取一份样例的内容副本（调用方改了也不影响别处）。 */
export function samplePayload<T>(id: string): T {
  return structuredClone(sample(id).payload) as T;
}
