import { describe, expect, it } from "vitest";
import { ApiError } from "../src/api/errors";
import { samplePayload } from "../src/contract/samples";
import type { DraftView, ServiceError, UserInputDraft } from "../src/contract/types";
import { Autosaver, type SaveStatus, type Timers } from "../src/draft/autosave";

/** 手动推进的计时器：只在测试里调 tick()。 */
function manualTimers() {
  let now = 0;
  const jobs = new Map<number, { at: number; fn: () => void }>();
  let id = 0;
  const timers: Timers = {
    set: (fn, ms) => {
      id += 1;
      jobs.set(id, { at: now + ms, fn });
      return id;
    },
    clear: (h) => {
      jobs.delete(h as number);
    },
  };
  const tick = async (ms: number) => {
    now += ms;
    for (const [k, j] of [...jobs.entries()].sort((a, b) => a[1].at - b[1].at)) {
      if (j.at <= now) {
        jobs.delete(k);
        j.fn();
      }
    }
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  };
  return { timers, tick, pending: () => jobs.size };
}

const base = samplePayload<DraftView>("62");
const edit = (d: UserInputDraft, text: string): UserInputDraft => ({ ...d, free_text: { state: "provided", text } });
const viewOf = (d: UserInputDraft, revision: number): DraftView => ({ ...base, draft: { ...d, revision } });
const serviceError = (code: ServiceError["code"], status: number, retry?: number) =>
  new ApiError("service", {
    status,
    service: { code, status, message_zh: "x", impact_zh: "x", next_action_zh: "x", retry_after_seconds: retry ?? null },
  });

function setup(save: (d: UserInputDraft, rev: number) => Promise<DraftView>, load?: () => Promise<DraftView>) {
  const t = manualTimers();
  const statuses: SaveStatus[] = [];
  const calls: { draft: UserInputDraft; rev: number }[] = [];
  const saver = new Autosaver(base, {
    save: (d, rev) => {
      calls.push({ draft: d, rev });
      return save(d, rev);
    },
    load: load ?? (() => Promise.reject(new Error("no load"))),
    onStatus: (s) => statuses.push(s),
    delayMs: 1500,
    timers: t.timers,
  });
  return { saver, calls, statuses, ...t };
}

describe("自动保存", () => {
  it("停手 1.5 秒才存，只存最后的内容；发送的修订号就是服务器当时的修订号", async () => {
    const r0 = base.draft.revision;
    const { saver, calls, tick } = setup((d, rev) => Promise.resolve(viewOf(d, rev + 1)));
    saver.update(edit(base.draft, "a"));
    await tick(500);
    saver.update(edit(base.draft, "ab"));
    await tick(500);
    saver.update(edit(base.draft, "abc"));
    await tick(1000);
    expect(calls).toHaveLength(0);
    await tick(600);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.draft.free_text.text).toBe("abc");
    expect(calls[0]?.rev).toBe(r0);
    expect(calls[0]?.draft.revision).toBe(r0);
    expect(saver.status).toEqual({ kind: "saved", revision: r0 + 1 });
  });

  it("同一时间只有一个保存请求；存的过程中又改了，存完用新的修订号再存", async () => {
    let release: (v: DraftView) => void = () => undefined;
    const { saver, calls, tick } = setup(
      (d, rev) =>
        new Promise<DraftView>((res) => {
          release = () => res(viewOf(d, rev + 1));
        }),
    );
    const r0 = base.draft.revision;
    saver.update(edit(base.draft, "one"));
    await tick(1500);
    expect(calls).toHaveLength(1);
    saver.update(edit(base.draft, "two"));
    await tick(1500);
    expect(calls).toHaveLength(1);
    release(viewOf(edit(base.draft, "one"), r0 + 1));
    await tick(0);
    await tick(1500);
    expect(calls).toHaveLength(2);
    expect(calls[1]?.rev).toBe(r0 + 1);
    expect(calls[1]?.draft.free_text.text).toBe("two");
  });

  it("内容改回服务器那一版：不保存", async () => {
    const { saver, calls, tick } = setup((d, rev) => Promise.resolve(viewOf(d, rev + 1)));
    saver.update(edit(base.draft, "x"));
    saver.update(base.draft);
    await tick(3000);
    expect(calls).toHaveLength(0);
    expect(saver.status.kind).toBe("saved");
  });

  it("409 冲突且服务器内容不同：停在冲突，不再自动保存；“用我的内容”以服务器修订号重存", async () => {
    const r0 = base.draft.revision;
    const server = viewOf(edit(base.draft, "from another tab"), r0 + 1);
    let first = true;
    const { saver, calls, tick } = setup(
      (d, rev) => {
        if (first) {
          first = false;
          return Promise.reject(serviceError("revision_conflict", 409));
        }
        return Promise.resolve(viewOf(d, rev + 1));
      },
      () => Promise.resolve(server),
    );
    saver.update(edit(base.draft, "mine"));
    await tick(1500);
    await tick(0);
    expect(saver.status.kind).toBe("conflict");
    saver.update(edit(base.draft, "mine, more"));
    await tick(5000);
    expect(calls).toHaveLength(1);
    saver.keepMine(server);
    await tick(0);
    await tick(0);
    expect(calls).toHaveLength(2);
    expect(calls[1]?.rev).toBe(r0 + 1);
    expect(calls[1]?.draft.free_text.text).toBe("mine, more");
  });

  it("409 但服务器已经是同样的内容（上一次回复丢了）：直接采用，不打扰用户", async () => {
    const r0 = base.draft.revision;
    const mine = edit(base.draft, "same");
    const { saver, tick } = setup(
      () => Promise.reject(serviceError("revision_conflict", 409)),
      () => Promise.resolve(viewOf(mine, r0 + 1)),
    );
    saver.update(mine);
    await tick(1500);
    await tick(0);
    expect(saver.status).toEqual({ kind: "saved", revision: r0 + 1 });
  });

  it("429 按服务给的秒数再试；断网按退避再试，改动一直留着", async () => {
    let n = 0;
    const { saver, calls, tick } = setup((d, rev) => {
      n += 1;
      if (n === 1) return Promise.reject(serviceError("rate_limited", 429, 7));
      if (n === 2) return Promise.reject(new ApiError("network"));
      return Promise.resolve(viewOf(d, rev + 1));
    });
    saver.update(edit(base.draft, "x"));
    await tick(1500);
    expect(saver.status).toMatchObject({ kind: "rate_limited", seconds: 7 });
    await tick(7000);
    expect(calls).toHaveLength(2);
    expect(saver.status).toMatchObject({ kind: "error", retryInMs: 5000 });
    await tick(5000);
    expect(calls).toHaveLength(3);
    expect(saver.status.kind).toBe("saved");
  });

  it("flush 立刻保存并等它结束（进入审阅前用）", async () => {
    const { saver, calls } = setup((d, rev) => Promise.resolve(viewOf(d, rev + 1)));
    saver.update(edit(base.draft, "now"));
    const st = await saver.flush();
    expect(calls).toHaveLength(1);
    expect(st.kind).toBe("saved");
  });
});
