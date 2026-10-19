// @vitest-environment node
// 共享结构一变，契约核对就失败：在临时副本上改动，不碰仓库里的 schema。
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.resolve(web, "..", "contracts", "product", "v2");
let dir = "";

function runCheck(contracts: string): void {
  execFileSync(process.execPath, ["scripts/contract-check.mjs", "--contracts", contracts], { cwd: web, stdio: "pipe" });
}

function copy(): string {
  dir = mkdtempSync(path.join(tmpdir(), "e5a-contract-"));
  cpSync(source, dir, { recursive: true });
  return dir;
}

afterEach(() => {
  if (dir) rmSync(dir, { recursive: true, force: true });
  dir = "";
});

describe("contract:check", () => {
  it("没改动的副本：通过", () => {
    expect(() => runCheck(copy())).not.toThrow();
  });

  it("schema 改了一个字节而 index.json 没跟着更新：失败", () => {
    const d = copy();
    const f = path.join(d, "RunReceipt.schema.json");
    writeFileSync(f, `${readFileSync(f, "utf8")} `);
    expect(() => runCheck(d)).toThrow();
  });

  it("schema 与 index.json 都更新了，但生成的类型还是旧的：失败（要先重新生成、再检查页面）", () => {
    const d = copy();
    const f = path.join(d, "RunReceipt.schema.json");
    const schema = JSON.parse(readFileSync(f, "utf8"));
    schema.properties.idempotency_key.description = "新加的说明";
    const text = `${JSON.stringify(schema, null, 1)}\n`;
    writeFileSync(f, text);
    const index = JSON.parse(readFileSync(path.join(d, "index.json"), "utf8"));
    index.messages.RunReceipt.sha256 = createHash("sha256").update(text).digest("hex");
    writeFileSync(path.join(d, "index.json"), JSON.stringify(index, null, 1));
    expect(() => runCheck(d)).toThrow();
  });
});
