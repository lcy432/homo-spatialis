// 草稿的自动保存（不依赖 React，单独测试）。
//
// * 停手 delayMs 之后才保存；同一时间只有一个保存请求；保存期间又改了，存完之后用新的修订号再存一次。
// * 发送时写上当时服务器的修订号（DraftSaveRequest：expected_revision 必须等于 draft.revision），服务器存下后修订号加一。
// * 409 revision_conflict：先读服务器上那一版。内容与本地完全相同（例如上一次回复丢了、其实已经存上）就直接采用；不同就停在
//   “冲突”，不再自动保存，由用户选：改用服务器的版本，或者把本地内容存成新的一版。不会最后写入者自动获胜。
// * 429：按服务给的秒数再试；断网：按退避再试（5、10、20、30 秒……），这期间改动一直留在页面里。
// * 服务修订号只来自服务器，不当本地编辑计数。
import type { DraftView, UserInputDraft } from "../contract/types";
import { ApiError, isApiError } from "../api/errors";
import { contentKey } from "./draft";

export type SaveStatus =
  | { kind: "saved"; revision: number }
  | { kind: "dirty"; revision: number }
  | { kind: "saving"; revision: number }
  | { kind: "error"; revision: number; error: ApiError; retryInMs: number | null }
  | { kind: "rate_limited"; revision: number; seconds: number }
  | { kind: "conflict"; revision: number; server: DraftView };

export interface Timers {
  set(fn: () => void, ms: number): unknown;
  clear(handle: unknown): void;
}

const realTimers: Timers = {
  set: (fn, ms) => window.setTimeout(fn, ms),
  clear: (h) => window.clearTimeout(h as number),
};

export interface AutosaverOptions {
  save(draft: UserInputDraft, expectedRevision: number): Promise<DraftView>;
  load(): Promise<DraftView>;
  onStatus(status: SaveStatus): void;
  /** 服务器存下了一版（基准修订号变了）。 */
  onSaved?(view: DraftView): void;
  delayMs?: number;
  timers?: Timers;
}

const BACKOFF = [5000, 10_000, 20_000, 30_000];

export class Autosaver {
  private base: { revision: number; key: string };
  private latest: UserInputDraft;
  private timer: unknown = null;
  private inflight: Promise<void> | null = null;
  private failures = 0;
  private disposed = false;
  private _status: SaveStatus;
  private readonly delayMs: number;
  private readonly timers: Timers;

  constructor(
    initial: DraftView,
    private readonly opts: AutosaverOptions,
  ) {
    this.base = { revision: initial.draft.revision, key: contentKey(initial.draft) };
    this.latest = initial.draft;
    this.delayMs = opts.delayMs ?? 1500;
    this.timers = opts.timers ?? realTimers;
    this._status = { kind: "saved", revision: initial.draft.revision };
  }

  get status(): SaveStatus {
    return this._status;
  }

  get revision(): number {
    return this.base.revision;
  }

  /** 页面里的内容与服务器最后存下的一版相同。 */
  get clean(): boolean {
    return contentKey(this.latest) === this.base.key;
  }

  private setStatus(s: SaveStatus): void {
    this._status = s;
    if (!this.disposed) this.opts.onStatus(s);
  }

  private schedule(ms: number): void {
    if (this.timer !== null) this.timers.clear(this.timer);
    this.timer = this.timers.set(() => {
      this.timer = null;
      void this.run();
    }, ms);
  }

  /** 每次本地改动之后调用（传入当前的整份草稿）。 */
  update(draft: UserInputDraft): void {
    this.latest = draft;
    if (this._status.kind === "conflict") return;
    if (this.clean) {
      if (this.timer !== null) this.timers.clear(this.timer);
      this.timer = null;
      if (!this.inflight) this.setStatus({ kind: "saved", revision: this.base.revision });
      return;
    }
    if (this._status.kind === "rate_limited" || (this._status.kind === "error" && this._status.retryInMs !== null)) return;
    if (!this.inflight) this.setStatus({ kind: "dirty", revision: this.base.revision });
    this.schedule(this.delayMs);
  }

  /** 立刻保存（进入审阅前、离开前）。返回之后的状态。 */
  async flush(): Promise<SaveStatus> {
    if (this.timer !== null) {
      this.timers.clear(this.timer);
      this.timer = null;
    }
    while (this.inflight) await this.inflight;
    if (this._status.kind !== "conflict" && !this.clean) await this.run();
    return this._status;
  }

  /** 用户点“重试保存”。 */
  retryNow(): void {
    if (this._status.kind === "conflict") return;
    this.failures = 0;
    this.schedule(0);
  }

  /** 冲突时改用服务器的版本：基准换成它（页面要把这份内容载入编辑器）。 */
  useServer(view: DraftView): void {
    this.base = { revision: view.draft.revision, key: contentKey(view.draft) };
    this.latest = view.draft;
    this.failures = 0;
    this.setStatus({ kind: "saved", revision: view.draft.revision });
  }

  /** 冲突时把本地内容存成新的一版：以服务器现在的修订号为基准再存。 */
  keepMine(view: DraftView): void {
    this.base = { revision: view.draft.revision, key: contentKey(view.draft) };
    this.failures = 0;
    this.setStatus({ kind: "dirty", revision: view.draft.revision });
    this.schedule(0);
  }

  dispose(): void {
    this.disposed = true;
    if (this.timer !== null) this.timers.clear(this.timer);
    this.timer = null;
  }

  private run(): Promise<void> {
    if (this.inflight) return this.inflight;
    const p = this.saveOnce().finally(() => {
      this.inflight = null;
    });
    this.inflight = p;
    return p;
  }

  private async saveOnce(): Promise<void> {
    if (this.disposed || this._status.kind === "conflict" || this.clean) {
      if (!this.disposed && this._status.kind !== "conflict") this.setStatus({ kind: "saved", revision: this.base.revision });
      return;
    }
    const sending = this.latest;
    const expected = this.base.revision;
    this.setStatus({ kind: "saving", revision: expected });
    try {
      const view = await this.opts.save({ ...sending, revision: expected }, expected);
      if (this.disposed) return;
      this.failures = 0;
      this.base = { revision: view.draft.revision, key: contentKey({ ...sending, revision: view.draft.revision }) };
      this.opts.onSaved?.(view);
      if (this.clean) this.setStatus({ kind: "saved", revision: this.base.revision });
      else {
        this.setStatus({ kind: "dirty", revision: this.base.revision });
        this.schedule(this.delayMs);
      }
    } catch (e) {
      if (this.disposed) return;
      const err = isApiError(e) ? e : new ApiError("network");
      if (err.kind === "stale") return;
      if (err.code === "revision_conflict") {
        await this.onConflict(err);
        return;
      }
      if (err.code === "rate_limited") {
        const seconds = err.retryAfter ?? 5;
        this.setStatus({ kind: "rate_limited", revision: this.base.revision, seconds });
        this.schedule(seconds * 1000);
        return;
      }
      if (err.kind === "network") {
        const wait = BACKOFF[Math.min(this.failures, BACKOFF.length - 1)] as number;
        this.failures += 1;
        this.setStatus({ kind: "error", revision: this.base.revision, error: err, retryInMs: wait });
        this.schedule(wait);
        return;
      }
      // 其它错误（结构不符、草稿已删除、内容太大……）：不自动重试，留给页面说明
      this.setStatus({ kind: "error", revision: this.base.revision, error: err, retryInMs: null });
    }
  }

  private async onConflict(err: ApiError): Promise<void> {
    let server: DraftView;
    try {
      server = await this.opts.load();
    } catch {
      this.setStatus({ kind: "error", revision: this.base.revision, error: err, retryInMs: null });
      return;
    }
    if (this.disposed) return;
    if (contentKey(server.draft) === contentKey({ ...this.latest, revision: server.draft.revision })) {
      // 服务器上已经是同样的内容（上一次回复丢了）：直接采用，不是真正的冲突
      this.base = { revision: server.draft.revision, key: contentKey(server.draft) };
      this.failures = 0;
      this.opts.onSaved?.(server);
      this.setStatus({ kind: "saved", revision: server.draft.revision });
      return;
    }
    this.setStatus({ kind: "conflict", revision: this.base.revision, server });
  }
}
