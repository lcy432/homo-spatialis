// E5-B 的位置：接 E3-C 提供的服务入口（校验、预检查、提交、查询、恢复与结果）。E5-A 不实现网络调用。
//
// 接上之后：
// * 方法签名与 SampleAdapter 相同，页面一行不改；
// * 每份回复照样按 E3 的 schema 校验；
// * 提交只走一次明确的请求，失败时用原来的幂等键重发、取回同一次运行（需求 B4），不能把“重新查询”写成“再开一次模拟”；
// * 确认请求、进度里的“由谁处理”、恢复入口等先等 E3 定下消息（需求 B1–B3）。
import { ApiError, type ConfirmOutcome, type ConfirmRequest, type ProductApi } from "./types";
import type {
  ConfirmedRef,
  ProductCapabilities,
  ProductPreflight,
  ProductResult,
  ProfileReview,
  RunProgress,
  RunReceipt,
  RunSubmission,
  UserInputDraft,
} from "../contract/types";

const notYet = (what: string) => new ApiError("not_implemented", `E5-B 才接：${what}（等 E3-C 的服务入口）`);

export class HttpAdapter implements ProductApi {
  constructor(readonly baseUrl: string) {}
  capabilities(): Promise<ProductCapabilities> {
    return Promise.reject(notYet("能力清单"));
  }
  review(_draft: UserInputDraft): Promise<ProfileReview> {
    return Promise.reject(notYet("审阅草稿"));
  }
  confirm(_request: ConfirmRequest): Promise<ConfirmOutcome> {
    return Promise.reject(notYet("确认画像"));
  }
  preflight(_confirmed: ConfirmedRef): Promise<ProductPreflight> {
    return Promise.reject(notYet("运行前检查"));
  }
  submit(_submission: RunSubmission): Promise<RunReceipt> {
    return Promise.reject(notYet("提交运行"));
  }
  progress(_runId: string): Promise<RunProgress> {
    return Promise.reject(notYet("查询进度"));
  }
  result(_runId: string): Promise<ProductResult> {
    return Promise.reject(notYet("读取结果"));
  }
}
