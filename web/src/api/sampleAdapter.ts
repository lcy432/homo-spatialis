// E5-A 的业务访问层：按场景挑选 E3 的公共样例并原样返回。
//
// 它**不做任何业务判断**：不匹配职业、不换算金额、不审阅、不判定合计、不算 C / E / M 或范围，也不改写样例。样例里没有的情况返回
// “样例里没有这种情况”的错误，不编造回复。每份送进来的草稿与提交、每份返回的消息都按 E3 的 schema 校验。
// 它可以模拟网络条件（transport.ts），但那只影响“请求有没有送达”，不改变回复的内容。
import { sample, samplePayload } from "../contract/samples";
import type {
  ConfirmedProfile,
  ConfirmedRef,
  ProductCapabilities,
  ProductPreflight,
  ProductResult,
  ProfileReview,
  RunProgress,
  RunReceipt,
  RunSubmission,
  UserInputDraft,
  ValidatedName,
} from "../contract/types";
import { assertMessage } from "../contract/validate";
import { sameDraftContent } from "../draft/draft";
import { CAPABILITIES_SAMPLE, DEFAULT_SCENARIO, PROGRESS_BY_RUN, RESULT_BY_RUN, type Scenario, scenarioById } from "./scenarios";
import { getTransport, setTransport, simulateRequest } from "./transport";
import { ApiError, type ConfirmOutcome, type ConfirmRequest, type ProductApi } from "./types";

export interface SubmissionRecord {
  idempotencyKey: string;
  /** 返回给界面的样例编号；回执丢失时是 "lost"。 */
  replied: string;
  at: number;
}

function notInSamples(what: string): ApiError {
  return new ApiError("not_in_samples", `样例里没有这种情况：${what}`);
}

function payloadOf<T>(id: string, expected: ValidatedName): T {
  const s = sample(id);
  if (s.message !== expected) throw new Error(`样例 ${id} 是 ${s.message}，不是 ${expected}`);
  return assertMessage<T>(expected, samplePayload(id));
}

/** 适配器模拟的是服务端：服务端会记住收到过的提交，所以登记存在本标签页里，刷新页面之后还在。 */
const REGISTRY_KEY = "e5a.adapter.v1";

interface StoredRegistry {
  scenarioId: string;
  firstKey: string | null;
  submissions: SubmissionRecord[];
}

function readRegistry(): StoredRegistry | null {
  try {
    const raw = sessionStorage.getItem(REGISTRY_KEY);
    return raw ? (JSON.parse(raw) as StoredRegistry) : null;
  } catch {
    return null;
  }
}

export class SampleAdapter implements ProductApi {
  private current: Scenario = scenarioById(DEFAULT_SCENARIO);
  /** 本场景收到过的提交（测试用它检查界面重复提交时用的是同一个幂等键）。 */
  readonly submissions: SubmissionRecord[] = [];
  /** 本场景收到过的进度查询。 */
  readonly progressQueries: string[] = [];
  private firstKey: string | null = null;
  private lostConnection = false;

  get scenario(): Scenario {
    return this.current;
  }

  /** 换一个样例场景：重新开始一段“服务端”记录。 */
  setScenario(id: string): void {
    this.current = scenarioById(id);
    this.submissions.length = 0;
    this.progressQueries.length = 0;
    this.firstKey = null;
    this.lostConnection = false;
    this.saveRegistry();
  }

  /** 页面加载时用：同一个场景就接着用本标签页里存着的提交登记（像服务端一样记得），否则重新开始。 */
  restoreOrStart(id: string): void {
    const stored = readRegistry();
    const scenario = scenarioById(id);
    if (stored && stored.scenarioId === scenario.id) {
      this.current = scenario;
      this.submissions.splice(0, this.submissions.length, ...stored.submissions);
      this.progressQueries.length = 0;
      this.firstKey = stored.firstKey;
      this.lostConnection = false;
    } else {
      this.setScenario(id);
    }
  }

  private saveRegistry(): void {
    try {
      const value: StoredRegistry = { scenarioId: this.current.id, firstKey: this.firstKey, submissions: this.submissions };
      sessionStorage.setItem(REGISTRY_KEY, JSON.stringify(value));
    } catch {
      /* 存不了就只在本页有效 */
    }
  }

  private async request(): Promise<void> {
    try {
      await simulateRequest();
    } catch (e) {
      this.lostConnection = true;
      throw e;
    }
  }

  async capabilities(): Promise<ProductCapabilities> {
    await this.request();
    return payloadOf<ProductCapabilities>(CAPABILITIES_SAMPLE, "ProductCapabilities");
  }

  async review(draft: UserInputDraft): Promise<ProfileReview> {
    assertMessage("UserInputDraft", draft);
    await this.request();
    const s = this.current;
    if (!s.review) throw notInSamples("这个场景没有审阅样例");
    if (s.draft && !sameDraftContent(draft, samplePayload<UserInputDraft>(s.draft))) {
      throw notInSamples("这份草稿不是样例草稿（改过的答案要等 E3 服务审阅）");
    }
    return payloadOf<ProfileReview>(s.review, "ProfileReview");
  }

  async confirm(request: ConfirmRequest): Promise<ConfirmOutcome> {
    assertMessage("UserInputDraft", request.draft);
    await this.request();
    const s = this.current;
    if (!s.confirmed) throw notInSamples("这个场景没有确认之后的画像样例");
    if (s.draft && !sameDraftContent(request.draft, samplePayload<UserInputDraft>(s.draft))) {
      throw notInSamples("这份草稿不是样例草稿（改过的答案要等 E3 服务确认）");
    }
    return { status: "confirmed", profile: payloadOf<ConfirmedProfile>(s.confirmed, "ConfirmedProfile") };
  }

  async preflight(confirmed: ConfirmedRef): Promise<ProductPreflight> {
    await this.request();
    const s = this.current;
    if (!s.preflight) throw notInSamples("这个场景没有预检查样例");
    const pre = payloadOf<ProductPreflight>(s.preflight, "ProductPreflight");
    if (s.confirmed && pre.confirmed.confirmed_id !== confirmed.confirmed_id) {
      throw notInSamples("预检查样例对应的不是这份确认画像");
    }
    return pre;
  }

  async submit(submission: RunSubmission): Promise<RunReceipt> {
    assertMessage("RunSubmission", submission);
    await this.request();
    const s = this.current;
    if (!s.receipts) throw notInSamples("这个场景没有提交样例");
    const key = submission.idempotency_key;
    let reply: string;
    if (s.receipts.first === "31") {
      reply = "31";
    } else if (this.firstKey === null) {
      this.firstKey = key;
      reply = s.receipts.first;
    } else if (key === this.firstKey && s.receipts.repeat) {
      reply = s.receipts.repeat;
    } else {
      this.submissions.push({ idempotencyKey: key, replied: "error", at: Date.now() });
      this.saveRegistry();
      throw notInSamples("样例只演示同一个幂等键的重复提交");
    }
    if (getTransport().dropNextReceipt) {
      setTransport({ dropNextReceipt: false });
      this.submissions.push({ idempotencyKey: key, replied: "lost", at: Date.now() });
      this.lostConnection = true;
      this.saveRegistry();
      throw new ApiError("network", "提交已经送达，但回执在网络上丢了");
    }
    this.submissions.push({ idempotencyKey: key, replied: reply, at: Date.now() });
    this.saveRegistry();
    return payloadOf<RunReceipt>(reply, "RunReceipt");
  }

  async progress(runId: string): Promise<RunProgress> {
    await this.request();
    this.progressQueries.push(runId);
    const ids = PROGRESS_BY_RUN[runId];
    if (!ids || !ids[0]) throw notInSamples(`运行 ${runId} 没有进度样例`);
    // 断线之后再查询，返回 E3 标明为契约示例的那份（内容与平时相同）
    const id = this.lostConnection && ids[1] ? ids[1] : ids[0];
    return payloadOf<RunProgress>(id, "RunProgress");
  }

  async result(runId: string): Promise<ProductResult> {
    await this.request();
    const id = RESULT_BY_RUN[runId];
    if (!id) throw notInSamples(`运行 ${runId} 没有结果样例`);
    return payloadOf<ProductResult>(id, "ProductResult");
  }
}
