// npm run contract:gen —— 从 contracts/product/v2/*.schema.json 生成 web/src/contract/generated/contracts.ts。
// 先核对 index.json 的哈希：哈希对不上说明 schema 被改过却没有重新导出，先找 E3，不生成。
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { GENERATED_FILE, contractDirFromArgs, generateSource, hashProblems } from "./contract-lib.mjs";

const dir = contractDirFromArgs();
const problems = hashProblems(dir);
if (problems.length) {
  console.error("共享结构的哈希与 index.json 不一致，不生成类型：");
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
const source = await generateSource(dir);
mkdirSync(path.dirname(GENERATED_FILE), { recursive: true });
writeFileSync(GENERATED_FILE, source, { encoding: "utf8" });
console.log(`已生成 ${path.relative(process.cwd(), GENERATED_FILE)}（${source.split("\n").length} 行）`);
