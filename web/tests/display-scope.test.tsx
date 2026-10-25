// 显示附层的页面接法（display/scope + <Zh>）：英文界面在作用域里按原文拿译文；缺译显示原文并标“未翻译”；知悉项按 ack_id、
// 作答状态按 (answer, state) 取；业务状态只从 source 读。中文界面不受作用域影响。
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { samplePayload } from "../src/contract/samples";
import type { DisplayEnvelope, DraftView, ProductCapabilities, ReviewReply } from "../src/contract/types";
import { DisplayScope, buildScope, findAcknowledgement, findAnswerState, lookupText, resolveDisplayText } from "../src/display/scope";
import { BizNotice, DText, Zh } from "../src/i18n/biz";
import { i18n } from "../src/i18n/index";

const reviewEn = samplePayload<DisplayEnvelope>("133");
const capsEn = samplePayload<DisplayEnvelope>("130");
const intentPartial = samplePayload<DisplayEnvelope>("122");

describe("作用域里按原文找译文", () => {
  it("审阅信封（样例 133）：职业名称、维度名称按原文给出英文；业务状态仍只在 source 里", () => {
    const scope = buildScope(reviewEn);
    const src = reviewEn.source as ReviewReply;
    const label = src.review.occupation.candidates?.[0]?.label as string;
    expect(lookupText([scope], label)).toMatchObject({ text: "Software Developers", provided: true });
    expect(lookupText([scope], src.review.six_dimensions.lines?.[1]?.label as string).text).toBe("Safety");
    // 按位置找优先
    expect(lookupText([scope], label, "/review/occupation/candidates/0/label").provided).toBe(true);
    // 作用域里没有的原文：照原文，reason null
    expect(lookupText([scope], "不存在的一段")).toEqual({ text: "不存在的一段", provided: false, reason: null, field: null });
    expect(src.review.occupation.status).toBe("catalog_selected");
  });

  it("同一原文在两个位置译文不同 → 有歧义，照原文显示；译文相同就正常给出", () => {
    const env: DisplayEnvelope = {
      display_schema_version: "e3-display-v2",
      kind: "runs",
      locale: "en",
      source: { a: "文员", b: "文员", c: "厨师", d: "厨师" } as unknown as DisplayEnvelope["source"],
      source_view_fingerprint: "x",
      status: "complete",
      fields: [
        { pointer: "/a", key: "k1", status: "provided", text: "Clerk A", source_locale: "zh" },
        { pointer: "/b", key: "k2", status: "provided", text: "Clerk B", source_locale: "zh" },
        { pointer: "/c", key: "k3", status: "provided", text: "Cook", source_locale: "zh" },
        { pointer: "/d", key: "k4", status: "provided", text: "Cook", source_locale: "zh" },
      ],
    };
    const scope = buildScope(env);
    expect(lookupText([scope], "文员")).toMatchObject({ text: "文员", provided: false, reason: "ambiguous" });
    expect(lookupText([scope], "厨师")).toMatchObject({ text: "Cook", provided: true });
    // 按位置找不受歧义影响
    expect(lookupText([scope], "文员", "/b").text).toBe("Clerk B");
  });

  it("嵌套作用域：由内向外找（内层审阅没有的目录名称从外层能力清单拿）", () => {
    const inner = buildScope(reviewEn);
    const outer = buildScope(capsEn);
    const caps = capsEn.source as ProductCapabilities;
    const group = caps.input_catalog?.groups.find((g) => g.group_id === "it_tech")?.label as string;
    expect(lookupText([inner], group).provided).toBe(false);
    expect(lookupText([inner, outer], group).provided).toBe(true);
  });

  it("缺译（受控夹具 122）：原文 + 服务报的原因", () => {
    const scope = buildScope(intentPartial);
    const src = intentPartial.source as { stage_zh: string };
    const r = lookupText([scope], src.stage_zh, "/stage_zh");
    expect(r.provided).toBe(false);
    expect(r.text).toBe(src.stage_zh);
    expect(r.reason).toBe("translation_missing");
  });
});

describe("知悉项与作答状态", () => {
  it("知悉项按 ack_id 找（样例 126），与 pending_confirmations 一一对应；页面不自动勾选（只是说明）", () => {
    const env = samplePayload<DisplayEnvelope>("126");
    const scope = buildScope(env);
    const pending = (env.source as ReviewReply).review.pending_confirmations ?? [];
    expect(pending.length).toBeGreaterThan(0);
    for (const id of pending) {
      const a = findAcknowledgement([scope], id);
      expect(a?.ack_id).toBe(id);
      expect(a?.text.en).toBeTruthy();
      expect(a?.text.zh).toBeTruthy();
    }
    expect(findAcknowledgement([scope], "ack:nothing")).toBeNull();
  });

  it("作答状态按 (answer, state) 找（样例 127），不按顺序；两种语言都有", () => {
    const env = samplePayload<DisplayEnvelope>("127");
    const scope = buildScope(env);
    const a = findAnswerState([scope], "taste", "no_preference");
    expect(a?.name.zh).toBe("明确没有口味偏好");
    expect(a?.name.en).toBeTruthy();
    expect(findAnswerState([scope], "leisure", "no_preference")).toBeNull();
    expect(resolveDisplayText(a?.name, "zh")?.text).toBe("明确没有口味偏好");
    expect(resolveDisplayText(a?.name, "en")).toMatchObject({ text: a?.name.en, provided: true });
    expect(resolveDisplayText({ key: "k", status: "missing", zh: "原文", en: null, missing_reason: "translation_missing" }, "en")).toMatchObject({
      text: "原文",
      provided: false,
      reason: "translation_missing",
    });
  });

  it("草稿迁移信封（样例 149）：迁移说明只从 source 读，不从 fields 推断", () => {
    const env = samplePayload<DisplayEnvelope>("149");
    const m = (env.source as DraftView).migration;
    expect(m?.consent_required).toBe(true);
    expect(m?.removed?.[0]?.ref).toBe("hr.commute");
  });
});

describe("<Zh> 在作用域里", () => {
  it("英文界面：有译文显示英文；缺译显示原文并标“未翻译”；中文界面原样显示", async () => {
    const src = reviewEn.source as ReviewReply;
    const label = src.review.occupation.candidates?.[0]?.label as string;
    await i18n.changeLanguage("en");
    const { rerender } = render(
      <DisplayScope envelope={reviewEn}>
        <Zh>{label}</Zh>
        <Zh>不在信封里的原文</Zh>
        <BizNotice />
      </DisplayScope>,
    );
    expect(screen.getByText("Software Developers")).toBeInTheDocument();
    const raw = screen.getByText(/不在信封里的原文/);
    expect(raw).toHaveAttribute("lang", "zh-CN");
    expect(raw).toHaveAttribute("data-missing", "no_field");
    expect(raw.textContent).toContain("(not translated)");
    // 信封英文完整：不再显示“这一部分有中文原文”的整块说明
    expect(screen.queryByRole("note")).toBeNull();

    rerender(
      <DisplayScope envelope={intentPartial}>
        <Zh>{(intentPartial.source as { stage_zh: string }).stage_zh}</Zh>
        <BizNotice />
      </DisplayScope>,
    );
    expect(screen.getByRole("note")).toHaveAttribute("data-missing-count", String((intentPartial.fields ?? []).filter((f) => f.status === "missing").length));
    expect(screen.getByText((intentPartial.source as { stage_zh: string }).stage_zh, { exact: false }).closest("[data-missing]")).toHaveAttribute(
      "data-missing",
      "translation_missing",
    );

    await i18n.changeLanguage("zh");
    rerender(
      <DisplayScope envelope={reviewEn}>
        <Zh>{label}</Zh>
        <DText text={{ key: "k", status: "provided", zh: "中文说明", en: "English note" }} />
      </DisplayScope>,
    );
    expect(screen.getByText(label)).not.toHaveAttribute("lang");
    expect(screen.getByText("中文说明")).toBeInTheDocument();
    expect(screen.queryByText("English note")).toBeNull();
  });
});
