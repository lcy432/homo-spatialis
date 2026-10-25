// @vitest-environment node
// 显示附层的读法：译文按位置取；缺译显示原文并说明原因；中文的 fields 逐字等于 source 里的原文；业务状态只从 source 读。
import { describe, expect, it } from "vitest";
import { samplePayload } from "../src/contract/samples";
import type { DisplayEnvelope, ProfileConfirmReply, RunIntentView, ServiceError } from "../src/contract/types";
import { attachmentFields, coverage, envelopeFields, pointerGet, resolveText } from "../src/display/fields";

describe("显示附层", () => {
  it("中文信封（样例 105）：每个 provided 字段的文字逐字等于 source 里那个位置的原文", () => {
    const env = samplePayload<DisplayEnvelope>("105");
    expect(env.locale).toBe("zh");
    const provided = (env.fields ?? []).filter((f) => f.status === "provided");
    expect(provided.length).toBeGreaterThan(10);
    for (const f of provided) expect(pointerGet(env.source, f.pointer), f.pointer).toBe(f.text);
  });

  it("英文信封（样例 106）：同一位置给出英文；业务数值仍从 source 读（阶段、期限、动作不在 fields 里）", () => {
    const env = samplePayload<DisplayEnvelope>("106");
    const zh = samplePayload<DisplayEnvelope>("105");
    expect(env.locale).toBe("en");
    const src = env.source as RunIntentView;
    const index = envelopeFields(env);
    const stage = resolveText(index, "/stage_zh", src.stage_zh);
    expect(stage.provided).toBe(true);
    expect(stage.text).not.toBe(src.stage_zh);
    // 同一位置：中文信封的原文就是业务对象里的字段
    expect(resolveText(envelopeFields(zh), "/stage_zh", (zh.source as RunIntentView).stage_zh).text).toBe((zh.source as RunIntentView).stage_zh);
    // 阶段、期限、允许的动作都只在 source 里
    expect(src.stage).toBe((zh.source as RunIntentView).stage);
    expect(src.expires_at).toBe((zh.source as RunIntentView).expires_at);
    expect(index.get("/stage")).toBeUndefined();
    expect(index.get("/allowed_actions")).toBeUndefined();
  });

  it("缺译文（受控夹具样例 122）：那个位置显示中文原文并给出原因；其余位置照常有英文", () => {
    const env = samplePayload<DisplayEnvelope>("122");
    const index = envelopeFields(env);
    const missing = (env.fields ?? []).filter((f) => f.status === "missing");
    expect(missing.length).toBeGreaterThan(0);
    for (const f of missing) {
      const original = String(pointerGet(env.source, f.pointer));
      const r = resolveText(index, f.pointer, original);
      expect(r.provided).toBe(false);
      expect(r.text).toBe(original);
      expect(r.missingReason).toBe(f.missing_reason);
    }
    const c = coverage(index);
    expect(c.missing).toBe(missing.length);
    expect(c.provided).toBeGreaterThan(0);
    expect(env.status).not.toBe("complete");
  });

  it("没有这个位置的字段：原文 + no_field；即时回复的附层按语言取（错误样例 92 带中英）", () => {
    const err = samplePayload<ServiceError>("92");
    const en = attachmentFields(err.display, "en");
    const zh = attachmentFields(err.display, "zh");
    expect(resolveText(en, "/message_zh", err.message_zh).provided).toBe(true);
    expect(resolveText(zh, "/message_zh", err.message_zh).text).toBe(err.message_zh);
    expect(resolveText(en, "/nowhere", "原文")).toEqual({ text: "原文", provided: false, missingReason: "no_field" });
    const confirm = samplePayload<ProfileConfirmReply>("68");
    expect(confirm.profile_summary?.expires_at).toBeTruthy();
    expect(attachmentFields(confirm.display, "en").size).toBeGreaterThan(0);
  });
});
