import { describe, expect, it, vi } from "vitest";
import { makeApi } from "../src/api/endpoints";
import { isApiError } from "../src/api/errors";
import type { HttpContext } from "../src/api/http";
import { samplePayload } from "../src/contract/samples";

function ctx(over: Partial<HttpContext> = {}) {
  let epoch = 1;
  const base: HttpContext = {
    csrf: () => "csrf-token-in-memory",
    epoch: () => epoch,
    onUnauthenticated: vi.fn(),
  };
  return { ctx: { ...base, ...over }, bump: () => (epoch += 1) };
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("HTTP 客户端", () => {
  it("修改请求带 CSRF，查询不带；换会话的那一次不带", async () => {
    const seen: { url: string; init: RequestInit }[] = [];
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      seen.push({ url, init });
      if (url.endsWith("/session") && init.method === "GET") return json(200, samplePayload("61"));
      if (url.endsWith("/session")) return json(200, samplePayload("61"));
      return json(201, samplePayload("62"));
    }) as unknown as typeof fetch;
    const { ctx: c } = ctx();
    const api = makeApi(c, fetchImpl);
    await api.session();
    await api.createDraft();
    await api.exchange("TEST_ONLY_invalid_invite_token_00000000000000");
    const h = (i: number) => seen[i]?.init.headers as Record<string, string>;
    expect(h(0)["X-CSRF-Token"]).toBeUndefined();
    expect(h(1)["X-CSRF-Token"]).toBe("csrf-token-in-memory");
    expect(h(2)["X-CSRF-Token"]).toBeUndefined();
    expect(seen.every((s) => s.init.credentials === "same-origin")).toBe(true);
    expect(seen.every((s) => s.url.startsWith("/api/v1/"))).toBe(true);
  });

  it("204（退出）不解析回复体", async () => {
    const fetchImpl = vi.fn(async () => new Response(null, { status: 204 })) as unknown as typeof fetch;
    await expect(makeApi(ctx().ctx, fetchImpl).signOut()).resolves.toBeUndefined();
  });

  it("2xx 的内容不符合共享结构：不使用，报结构不符", async () => {
    const fetchImpl = vi.fn(async () => json(200, { csrf_token: 1 })) as unknown as typeof fetch;
    const err = await makeApi(ctx().ctx, fetchImpl).session().catch((e) => e);
    expect(isApiError(err) && err.kind).toBe("contract");
  });

  it("服务错误按 ServiceError 读出；401 通知会话层（带发出时的代次）", async () => {
    const fetchImpl = vi.fn(async () => json(401, samplePayload("92"))) as unknown as typeof fetch;
    const { ctx: c } = ctx();
    const err = await makeApi(c, fetchImpl).session().catch((e) => e);
    expect(isApiError(err) && err.kind).toBe("service");
    expect(isApiError(err) && err.code).toBe("unauthenticated");
    expect(c.onUnauthenticated).toHaveBeenCalledWith(1);
  });

  it("429 带服务给的等待秒数", async () => {
    const fetchImpl = vi.fn(async () => json(429, samplePayload("94"))) as unknown as typeof fetch;
    const err = await makeApi(ctx().ctx, fetchImpl).runs().catch((e) => e);
    expect(isApiError(err) && err.code).toBe("rate_limited");
    expect(isApiError(err) && err.retryAfter).toBeGreaterThan(0);
  });

  it("代理给的非 JSON 错误页（服务没起来）：当成连不上，不当业务错误", async () => {
    const fetchImpl = vi.fn(async () => new Response("<html>Bad Gateway</html>", { status: 502 })) as unknown as typeof fetch;
    const err = await makeApi(ctx().ctx, fetchImpl).runs().catch((e) => e);
    expect(isApiError(err) && err.kind).toBe("network");
  });

  it("回复到达时身份已经换了：丢弃（stale），不写进任何地方", async () => {
    const { ctx: c, bump } = ctx();
    const fetchImpl = vi.fn(async () => {
      bump();
      return json(200, samplePayload("61"));
    }) as unknown as typeof fetch;
    const err = await makeApi(c, fetchImpl).session().catch((e) => e);
    expect(isApiError(err) && err.kind).toBe("stale");
  });

  it("请求体不符合共享结构：不发送", async () => {
    const fetchImpl = vi.fn() as unknown as typeof fetch;
    const err = await makeApi(ctx().ctx, fetchImpl)
      .submit("intent-1", { confirm_with: "x", idempotency_key: "short", accept_scope: true })
      .catch((e) => e);
    expect(isApiError(err) && err.kind).toBe("contract");
    expect(isApiError(err) && err.direction).toBe("request");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("准备模拟与确认开始：同时接受 200（原意图 / 重复提交）与 202，业务状态看内容", async () => {
    const intent = samplePayload("70");
    const reply = samplePayload("74");
    const fetchImpl = vi.fn(async (url: string) => (url.endsWith("/submit") ? json(200, reply) : json(202, intent))) as unknown as typeof fetch;
    const api = makeApi(ctx().ctx, fetchImpl);
    expect((await api.createIntent({ confirmed_id: "confirmed.x" })).status).toBe(202);
    const r = await api.submit("intent-1", { confirm_with: "a".repeat(64), idempotency_key: "idem-0123456789abcdef", accept_scope: true });
    expect(r.status).toBe(200);
    expect(r.data.receipt.status).toBe("duplicate_same_run");
  });
});
