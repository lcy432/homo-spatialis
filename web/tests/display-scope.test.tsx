// 显示附层的页面接法（display/scope + <Zh pointer>）：英文界面按字段位置取译文，并核对信封的 source 在那个位置就是正在显示的原文；
// 同名字段各取自己的译文、缺译不借别的字段或外层当前资源（E5-R3）；知悉项按 ack_id、作答状态按 (answer, state) 取；
// 业务状态只从 source 读；中文界面不受作用域影响；整块说明按实际显示的缺译字段数写。
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { samplePayload } from "../src/contract/samples";
import type { DisplayEnvelope, ProductCapabilities, ReviewReply } from "../src/contract/types";
import {
  buildScope,
  envelopeOf,
  envelopeOfAttachment,
  findAcknowledgement,
  findAnswerState,
  lookupText,
  resolveDisplayText,
  DisplayScope,
} from "../src/display/scope";
import { BizNotice, DText, Zh } from "../src/i18n/biz";
import { i18n } from "../src/i18n/index";

const reviewEn = samplePayload<DisplayEnvelope>("133");
const capsEn = samplePayload<DisplayEnvelope>("130");
const intentPartial = samplePayload<DisplayEnvelope>("122");
const scopeOf = (env: DisplayEnvelope) => buildScope(envelopeOf(env));

describe("按字段位置找译文", () => {
  it("审阅信封（样例 133）：按位置给出英文；位置上的原文对不上就不套；source 有原文而没有字段 = no_field；业务状态仍只在 source 里", () => {
    const scope = scopeOf(reviewEn);
    const src = reviewEn.source as ReviewReply;
    const label = src.review.occupation.candidates?.[0]?.label as string;
    expect(lookupText([scope], "/review/occupation/candidates/0/label", label)).toMatchObject({ text: "Software Developers", provided: true });
    expect(lookupText([scope], "/review/six_dimensions/lines/1/label", src.review.six_dimensions.lines?.[1]?.label as string).text).toBe("Safety");
    // 同一个位置、不同的原文（附层与业务对象不匹配）：不套文字
    expect(lookupText([scope], "/review/occupation/candidates/0/label", "别的原文")).toMatchObject({ text: "别的原文", provided: false, reason: null });
    // source 里有、fields 里没有的位置：原文 + no_field（不去别处借）
    expect(lookupText([scope], "/review/occupation/status", src.review.occupation.status)).toMatchObject({ provided: false, reason: "no_field" });
    expect(src.review.occupation.status).toBe("catalog_selected");
  });

  it("同名字段各取自己的译文：第二个“文员”缺译就照原文并给原因，不借第一个的英文；两个合法不同译文各自正确（独立核查 R3）", () => {
    const env = structuredClone(capsEn);
    const caps = env.source as ProductCapabilities;
    const a = "/input_catalog/occupations/0/label";
    const b = "/input_catalog/occupations/1/label";
    (caps.input_catalog as NonNullable<typeof caps.input_catalog>).occupations[0]!.label = "文员";
    (caps.input_catalog as NonNullable<typeof caps.input_catalog>).occupations[1]!.label = "文员";
    env.fields = [
      { pointer: a, key: "entity.job.a", status: "provided", text: "Office clerk", source_locale: "zh" },
      { pointer: b, key: "entity.job.b", status: "missing", text: null, missing_reason: "binding_unverified", source_locale: "zh" },
    ];
    const scope = scopeOf(env);
    expect(lookupText([scope], a, "文员")).toMatchObject({ text: "Office clerk", provided: true });
    expect(lookupText([scope], b, "文员")).toMatchObject({ text: "文员", provided: false, reason: "binding_unverified" });
    env.fields[1] = { pointer: b, key: "entity.job.b", status: "provided", text: "Administrative clerk", source_locale: "zh" };
    expect(lookupText([scopeOf(env)], b, "文员").text).toBe("Administrative clerk");
    expect(lookupText([scopeOf(env)], a, "文员").text).toBe("Office clerk");
  });

  it("嵌套作用域：内层的 source 没有这个位置才看外层；内层有同样原文却缺译时不借外层当前资源的文字", () => {
    const inner = scopeOf(reviewEn);
    const outer = scopeOf(capsEn);
    const caps = capsEn.source as ProductCapabilities;
    const group = caps.input_catalog?.groups.find((g) => g.group_id === "it_tech")?.label as string;
    const gp = `/input_catalog/groups/${caps.input_catalog?.groups.findIndex((g) => g.group_id === "it_tech")}/label`;
    expect(lookupText([inner], gp, group).provided).toBe(false);
    expect(lookupText([inner, outer], gp, group).provided).toBe(true);
    // 内层 unavailable（没有字段）、原文与外层相同：照原文 + no_field，不借外层
    const unavailable = structuredClone(capsEn);
    unavailable.fields = [];
    unavailable.status = "unavailable";
    const note = caps.meta.data_pack.note;
    expect(lookupText([scopeOf(unavailable), outer], "/meta/data_pack/note", note)).toMatchObject({ text: note, provided: false, reason: "no_field" });
    expect(lookupText([outer], "/meta/data_pack/note", note).provided).toBe(true);
  });

  it("缺译（受控夹具 122）：原文 + 服务报的原因", () => {
    const scope = scopeOf(intentPartial);
    const src = intentPartial.source as { stage_zh: string };
    const r = lookupText([scope], "/stage_zh", src.stage_zh);
    expect(r.provided).toBe(false);
    expect(r.text).toBe(src.stage_zh);
    expect(r.reason).toBe("translation_missing");
  });

  it("即时回复的附层（服务错误样例 170）：按当前语言取那组字段，业务对象就是这份回复", () => {
    const err = samplePayload<{ message_zh: string; display: Parameters<typeof envelopeOfAttachment>[1] }>("170");
    const en = envelopeOfAttachment(err, err.display, "en");
    expect(en).not.toBeNull();
    const r = lookupText([buildScope(en as NonNullable<typeof en>)], "/message_zh", err.message_zh);
    expect(r.provided).toBe(true);
    expect(r.text).toContain("settings preview");
  });
});

describe("知悉项与作答状态", () => {
  it("知悉项按 ack_id 找（样例 126），与 pending_confirmations 一一对应；页面不自动勾选（只是说明）", () => {
    const env = samplePayload<DisplayEnvelope>("126");
    const scope = scopeOf(env);
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
    const scope = scopeOf(env);
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
});

describe("<Zh pointer> 在作用域里", () => {
  it("英文界面：有译文显示英文；缺译显示原文并标“未翻译”；整块说明按实际显示的缺译数；中文界面原样显示", async () => {
    const src = reviewEn.source as ReviewReply;
    const label = src.review.occupation.candidates?.[0]?.label as string;
    await i18n.changeLanguage("en");
    const { rerender } = render(
      <DisplayScope envelope={reviewEn}>
        <Zh pointer="/review/occupation/candidates/0/label">{label}</Zh>
        <Zh pointer="/nowhere">不在信封里的原文</Zh>
        <BizNotice />
      </DisplayScope>,
    );
    expect(screen.getByText("Software Developers")).toBeInTheDocument();
    const raw = screen.getByText(/不在信封里的原文/);
    expect(raw).toHaveAttribute("lang", "zh-CN");
    expect(raw).toHaveAttribute("data-missing", "no_scope");
    expect(raw.textContent).toContain("(not translated)");
    // 作用域里没有这段原文：不算这个信封的缺译，整块说明不出现
    expect(screen.queryByRole("note")).toBeNull();

    const partialSrc = intentPartial.source as { stage_zh: string };
    rerender(
      <DisplayScope envelope={intentPartial}>
        <Zh pointer="/stage_zh">{partialSrc.stage_zh}</Zh>
        <BizNotice />
      </DisplayScope>,
    );
    expect(screen.getByRole("note")).toHaveAttribute("data-missing-count", "1");
    expect(screen.getByText(partialSrc.stage_zh, { exact: false }).closest("[data-missing]")).toHaveAttribute("data-missing", "translation_missing");

    await i18n.changeLanguage("zh");
    rerender(
      <DisplayScope envelope={reviewEn}>
        <Zh pointer="/review/occupation/candidates/0/label">{label}</Zh>
        <DText text={{ key: "k", status: "provided", zh: "中文说明", en: "English note" }} />
      </DisplayScope>,
    );
    expect(screen.getByText(label)).not.toHaveAttribute("lang");
    expect(screen.getByText("中文说明")).toBeInTheDocument();
    expect(screen.queryByText("English note")).toBeNull();
  });
});
