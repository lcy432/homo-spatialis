// npm run contract:check —— 共享结构变了就失败（build 与 test 之前自动执行）。
//   1. 每份 schema 文件的 SHA-256 与 index.json 一致，目录里没有未登记的 schema；
//   2. 按当前 schema 重新生成的类型与仓库里的 generated/contracts.ts 逐字相同。
// 可以用 --contracts <目录> 指向一份临时副本（验收“改一个字节就失败”时用，不改仓库里的 schema）。
import { readFileSync } from "node:fs";
import path from "node:path";
import { GENERATED_FILE, contractDirFromArgs, generateSource, hashProblems, readIndex } from "./contract-lib.mjs";

const dir = contractDirFromArgs();
const problems = hashProblems(dir);
if (!problems.length) {
  let current = "";
  try {
    current = readFileSync(GENERATED_FILE, "utf8").replace(/\r\n/g, "\n");
  } catch {
    problems.push(`${path.relative(process.cwd(), GENERATED_FILE)} 不存在：先运行 npm run contract:gen`);
  }
  if (current) {
    const fresh = await generateSource(dir);
    if (fresh !== current) problems.push("生成的类型与当前 schema 不一致：先运行 npm run contract:gen，再检查用到这些字段的页面");
  }
}
if (problems.length) {
  console.error("共享结构变了：先重新生成类型，再检查用到这些字段的页面。");
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
const count = Object.keys(readIndex(dir).messages).length;
console.log(`共享结构核对通过：${path.relative(process.cwd(), dir) || dir}（${count} 份 schema 的哈希与 index.json 一致，生成的类型是最新的）`);
