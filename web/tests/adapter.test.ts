import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SampleAdapter } from "../src/api/sampleAdapter";
import { PROGRESS_BY_RUN, RESULT_BY_RUN, runIdOf, SCENARIOS } from "../src/api/scenarios";
import { setTransport } from "../src/api/transport";
import { ApiError } from "../src/api/types";
import { samplePayload } from "../src/contract/samples";
import type { RunSubmission, UserInputDraft } from "../src/contract/types";

function sampleDraft(): UserInputDraft {
  const d = samplePayload<UserInputDraft>("02");
  delete (d.meta as { sample?: unknown }).sample;
  return d;
}

function submission(key: string): RunSubmission {
  const s = samplePayload<RunSubmission>("06");
  delete (s.meta as { sample?: unknown }).sample;
  return { ...s, idempotency_key: key };
}

describe("样例适配器：只挑选、原样返回样例", () => {
  let a: SampleAdapter;
  beforeEach(() => {
    setTransport({ latencyMs: 0, offline: false, dropNextReceipt: false });
    a = new SampleAdapter();
    a.setScenario("normal");
  });
  afterEach(() => setTransport({ latencyMs: 0, offline: false, dropNextReceipt: false }));

  it("同一个幂等键：第一次 07（接受），再次 08（同一个运行）", async () => {
    const r1 = await a.submit(submission("idem-test-0001"));
    const r2 = await a.submit(submission("idem-test-0001"));
    expect(r1.status).toBe("accepted");
    expect(r2.status).toBe("duplicate_same_run");
    expect(r2.run_id).toBe(r1.run_id);
    expect(a.submissions.map((s) => s.replied)).toEqual(["07", "08"]);
  });

  it("换了键：样例里没有这种情况（不编造一个新运行）", async () => {
    await a.submit(submission("idem-test-0001"));
    await expect(a.submit(submission("idem-test-0002"))).rejects.toMatchObject({ kind: "not_in_samples" });
  });

  it("回执丢失：提交已记下，界面收到网络错误；用同一个键重试拿到同一个运行", async () => {
    setTransport({ dropNextReceipt: true });
    await expect(a.submit(submission("idem-test-0003"))).rejects.toMatchObject({ kind: "network" });
    const r = await a.submit(submission("idem-test-0003"));
    expect(r.status).toBe("duplicate_same_run");
    expect(a.submissions.map((s) => s.replied)).toEqual(["lost", "08"]);
  });

  it("断网时请求没有送达，不记提交", async () => {
    setTransport({ offline: true });
    await expect(a.submit(submission("idem-test-0004"))).rejects.toBeInstanceOf(ApiError);
    expect(a.submissions).toHaveLength(0);
  });

  it("断线之后再查进度，返回 E3 标明为契约示例的那一份（32），内容与平时相同", async () => {
    const run = runIdOf("09");
    const p1 = await a.progress(run);
    expect(p1.meta.sample?.origin).toBe("offline_run");
    setTransport({ offline: true });
    await expect(a.progress(run)).rejects.toMatchObject({ kind: "network" });
    setTransport({ offline: false });
    const p2 = await a.progress(run);
    expect(p2.meta.sample?.origin).toBe("contract_example");
    expect(p2.clones).toEqual(p1.clones);
  });

  it("回执里的运行查得到进度与结果（样例重新生成之后也连得上）", async () => {
    const receipt = await a.submit(submission("idem-test-0006"));
    expect((await a.progress(receipt.run_id!)).run_id).toBe(receipt.run_id);
    expect((await a.result(receipt.run_id!)).run_id).toBe(receipt.run_id);
  });

  it("改过的草稿拿不到样例审阅", async () => {
    expect((await a.review(sampleDraft())).draft.draft_id).toBe("draft.u501");
    const edited = sampleDraft();
    edited.six_dimensions.entries!.leisure = { state: "provided", value_text: "15" };
    await expect(a.review(edited)).rejects.toMatchObject({ kind: "not_in_samples" });
  });

  it("按运行编号取结果；没有样例的运行明确报错", async () => {
    expect((await a.result(runIdOf("23"))).cities?.[1]?.complete).toBe(0);
    await expect(a.result("run-unknown")).rejects.toMatchObject({ kind: "not_in_samples" });
  });

  it("不合共享结构的提交被拒（多出字段）", async () => {
    await expect(a.submit({ ...submission("idem-test-0005"), extra: 1 } as unknown as RunSubmission)).rejects.toThrow(/不符合共享结构/);
  });
});

describe("样例场景表", () => {
  it("每个场景打开的运行都有对应的进度或结果样例", () => {
    for (const s of SCENARIOS) {
      if (s.entry === "progress") expect(PROGRESS_BY_RUN[s.runId!], s.id).toBeDefined();
      if (s.entry === "result") expect(RESULT_BY_RUN[s.runId!], s.id).toBeDefined();
    }
  });

  it("接受提交的回执指向一个有进度样例的运行", () => {
    for (const s of SCENARIOS) {
      for (const id of [s.receipts?.first, s.receipts?.repeat]) {
        if (!id) continue;
        const run = samplePayload<{ status: string; run_id?: string | null }>(id);
        if (run.status === "accepted" || run.status === "duplicate_same_run") {
          expect(PROGRESS_BY_RUN[run.run_id!], `${s.id} 的回执 ${id}`).toBeDefined();
          expect(run.run_id, `${s.id} 的回执 ${id}`).toBe(s.runId);
        }
      }
    }
  });
});
