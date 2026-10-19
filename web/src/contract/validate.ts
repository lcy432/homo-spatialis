// 按 E3 的 schema 校验消息结构。只检查、不改数据：不开 useDefaults（不把缺省值悄悄填进草稿）、不开 coerceTypes（不把 "30" 转成数字）、
// 不开 removeAdditional（多出的字段是错误，不是删掉了事）。业务判断仍归 E3。
import Ajv2020 from "ajv/dist/2020.js";
import type { ErrorObject, ValidateFunction } from "ajv";
import { schemaFor } from "./schemas";
import type { MessageName, ValidatedName } from "./types";

const ajv = new Ajv2020({ allErrors: true, strict: true });
const cache = new Map<ValidatedName, ValidateFunction>();

function compiled(name: ValidatedName): ValidateFunction {
  let fn = cache.get(name);
  if (!fn) {
    if (name === "YearFacts") {
      const result = schemaFor("ProductResult");
      fn = ajv.compile({ $defs: result.$defs, $ref: "#/$defs/YearFacts" });
    } else {
      fn = ajv.compile(schemaFor(name as MessageName));
    }
    cache.set(name, fn);
  }
  return fn;
}

function describe(errors: ErrorObject[] | null | undefined): string[] {
  return (errors ?? []).map((e) => `${e.instancePath || "(根)"} ${e.message ?? ""}`.trim());
}

export type CheckResult = { ok: true } | { ok: false; errors: string[] };

export function checkMessage(name: ValidatedName, payload: unknown): CheckResult {
  const fn = compiled(name);
  return fn(payload) ? { ok: true } : { ok: false, errors: describe(fn.errors) };
}

export class ContractError extends Error {
  readonly messageName: ValidatedName;
  readonly errors: string[];
  constructor(name: ValidatedName, errors: string[]) {
    super(`${name} 不符合共享结构：${errors.slice(0, 5).join("；")}`);
    this.name = "ContractError";
    this.messageName = name;
    this.errors = errors;
  }
}

/** 校验通过才返回；不通过就抛错（界面不显示不合规的消息，也不把不合规的草稿送出去）。 */
export function assertMessage<T>(name: ValidatedName, payload: unknown): T {
  const result = checkMessage(name, payload);
  if (!result.ok) throw new ContractError(name, result.errors);
  return payload as T;
}
