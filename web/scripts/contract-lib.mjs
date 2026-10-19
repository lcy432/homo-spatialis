// 契约层的共用函数：读取 E3 导出的共享结构（contracts/product/v2/），核对 index.json 里的哈希，生成 TypeScript 类型。
//
// 共享结构由 E3 唯一维护（src/common/product_contracts.py → contracts/product/v2/）。这里只读、不改；前端的类型全部由这里生成，
// 不手写第二套。导出的 schema 没写 $schema、引用是 #/$defs/…（pydantic 按 JSON Schema 2020-12 生成）。
import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { compile } from "json-schema-to-typescript";

export const WEB_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const DEFAULT_CONTRACT_DIR = path.resolve(WEB_ROOT, "..", "contracts", "product", "v2");
export const GENERATED_FILE = path.join(WEB_ROOT, "src", "contract", "generated", "contracts.ts");

export function contractDirFromArgs(argv = process.argv.slice(2)) {
  const i = argv.indexOf("--contracts");
  return i >= 0 && argv[i + 1] ? path.resolve(argv[i + 1]) : DEFAULT_CONTRACT_DIR;
}

export function readIndex(dir) {
  return JSON.parse(readFileSync(path.join(dir, "index.json"), "utf8"));
}

/** index.json 记的哈希与 schema 文件实际字节是否一致；目录里有没有 index.json 没登记的 schema。 */
export function hashProblems(dir) {
  const index = readIndex(dir);
  const problems = [];
  const listed = new Set();
  for (const [name, entry] of Object.entries(index.messages)) {
    listed.add(entry.file);
    let bytes;
    try {
      bytes = readFileSync(path.join(dir, entry.file));
    } catch {
      problems.push(`${name}: index.json 登记了 ${entry.file}，但目录里没有这个文件`);
      continue;
    }
    const got = createHash("sha256").update(bytes).digest("hex");
    if (got !== entry.sha256) {
      problems.push(
        `${entry.file}: index.json 记的 SHA-256 是 ${entry.sha256.slice(0, 12)}…，文件实际是 ${got.slice(0, 12)}…`,
      );
    }
  }
  for (const file of readdirSync(dir)) {
    if (file.endsWith(".schema.json") && !listed.has(file)) {
      problems.push(`${file}: 目录里有这份 schema，但 index.json 没有登记`);
    }
  }
  return problems;
}

function sortKeys(value) {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((k) => [k, sortKeys(value[k])]),
    );
  }
  return value;
}

const same = (a, b) => JSON.stringify(sortKeys(a)) === JSON.stringify(sortKeys(b));

/**
 * 去掉嵌套字段上的 title（只保留每个 $defs 条目顶层的 title）。title 只是注释性关键字，不影响校验；保留它会让生成器给每个字段
 * 起一个名字（State、State1、State2……），类型文件难读。这是唯一的预处理，description 保留成注释。
 */
function stripNestedTitles(node, isDefRoot) {
  if (Array.isArray(node)) return node.map((n) => stripNestedTitles(n, false));
  if (!node || typeof node !== "object") return node;
  const out = {};
  for (const [k, v] of Object.entries(node)) {
    if (k === "title" && !isDefRoot) continue;
    if (k === "properties" && v && typeof v === "object") {
      out[k] = Object.fromEntries(Object.entries(v).map(([p, s]) => [p, stripNestedTitles(s, false)]));
    } else if (k === "$defs") {
      out[k] = Object.fromEntries(Object.entries(v).map(([d, s]) => [d, stripNestedTitles(s, true)]));
    } else {
      out[k] = stripNestedTitles(v, false);
    }
  }
  return out;
}

/** 把 13 份 schema 合成一份：同名的 $defs 必须逐字相同（它们来自同一个 pydantic 类），否则报错，不挑一个用。 */
export function combinedSchema(dir) {
  const index = readIndex(dir);
  const defs = {};
  const conflicts = [];
  const names = Object.keys(index.messages).sort();
  for (const name of names) {
    const schema = JSON.parse(readFileSync(path.join(dir, index.messages[name].file), "utf8"));
    const { $defs = {}, ...top } = schema;
    for (const [k, v] of Object.entries($defs)) {
      if (k in defs && !same(defs[k], v)) conflicts.push(k);
      defs[k] = v;
    }
    if (name in defs && !same(defs[name], top)) conflicts.push(name);
    defs[name] = top;
  }
  const sortedDefs = Object.fromEntries(
    Object.keys(defs)
      .sort()
      .map((k) => [k, defs[k]]),
  );
  const root = {
    title: "ProductContracts",
    description: "全部 13 份共享消息（只为生成类型而合成的根，不是一份真实消息）",
    type: "object",
    additionalProperties: false,
    properties: Object.fromEntries(names.map((n) => [n, { $ref: `#/$defs/${n}` }])),
    required: names,
    $defs: sortedDefs,
  };
  return { root: stripNestedTitles(root, true), conflicts: [...new Set(conflicts)], index };
}

export async function generateSource(dir) {
  const { root, conflicts, index } = combinedSchema(dir);
  if (conflicts.length) {
    throw new Error(`共享结构里同名定义不一致：${conflicts.join("、")}（先找 E3 核对，不要在前端挑一个用）`);
  }
  const banner = [
    "/* eslint-disable */",
    "/**",
    ` * 由 web/scripts/contract-gen.mjs 从 contracts/product/v2/*.schema.json 生成（结构版本 ${index.schema_version}）。`,
    " * 不要手改：共享结构由 E3 维护（src/common/product_contracts.py），结构变了就重新运行 npm run contract:gen。",
    " */",
  ].join("\n");
  const source = await compile(root, "ProductContracts", {
    bannerComment: banner,
    additionalProperties: false,
    unreachableDefinitions: true,
    declareExternallyReferenced: true,
    format: true,
    style: { printWidth: 120, singleQuote: false, trailingComma: "all" },
  });
  return source.replace(/\r\n/g, "\n");
}
