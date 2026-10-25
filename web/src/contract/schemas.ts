// 运行时读取 E3 导出的 schema 与 index.json（别名 @contracts，默认是仓库的 contracts/product/v3/；构建时打进包里，不复制、不手改）。
// 换目录（例如 v4）时改 vite.config.ts 的默认值与 scripts/contract-lib.mjs 的 CONTRACT_SUBDIR。
import type { MessageName } from "./types";

const files = import.meta.glob<Record<string, unknown>>("@contracts/*.schema.json", {
  eager: true,
  import: "default",
});

const indexFiles = import.meta.glob<ContractIndex>("@contracts/index.json", {
  eager: true,
  import: "default",
});

export interface ContractIndex {
  schema_version: string;
  questionnaire_version: string;
  vocabulary_version: string;
  health_questionnaire_version: string;
  conversion_version: string;
  messages: Record<string, { file: string; sha256: string }>;
}

const byFile = new Map<string, Record<string, unknown>>();
for (const [p, schema] of Object.entries(files)) {
  byFile.set(p.split("/").pop() as string, schema);
}

const index = Object.values(indexFiles)[0];
if (!index) throw new Error("找不到共享结构的 index.json（@contracts）");
export const CONTRACT_INDEX: ContractIndex = index;

export function schemaFor(name: MessageName): Record<string, unknown> {
  const entry = CONTRACT_INDEX.messages[name];
  const schema = entry ? byFile.get(entry.file) : undefined;
  if (!schema) throw new Error(`共享结构里没有 ${name} 的 schema`);
  return schema;
}

export const MESSAGE_NAMES = Object.keys(CONTRACT_INDEX.messages).sort() as MessageName[];
