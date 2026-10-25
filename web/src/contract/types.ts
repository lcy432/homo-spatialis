// 页面用到的共享消息类型。全部来自 generated/contracts.ts（由 E3 的 schema 生成），这里只是集中导出，不另写字段。
import type { ProductContracts } from "./generated/contracts";

export type * from "./generated/contracts";

/** 共享结构里的消息名（就是 contracts/product/v3/index.json 的 messages；E3 增减消息后重新生成即可）。 */
export type MessageName = keyof ProductContracts;

/** 样例 44–47 是 ProductResult 里的 YearFacts 片段，不是一份完整消息。 */
export type ValidatedName = MessageName | "YearFacts";

export type AnswerState = "unanswered" | "provided" | "indifferent" | "unsure";
export type AllocationGroup = "six_dimensions" | "material";
