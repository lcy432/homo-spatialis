import { describe, expect, it } from "vitest";
import { SAMPLE_IDS, sample } from "../src/contract/samples";
import { CONTRACT_INDEX, MESSAGE_NAMES } from "../src/contract/schemas";
import { checkMessage } from "../src/contract/validate";

describe("共享结构与公共样例", () => {
  it("读到的是 v2 结构的 13 份消息", () => {
    expect(CONTRACT_INDEX.schema_version).toBe("e3a-product-schema-v2");
    expect(MESSAGE_NAMES).toHaveLength(13);
  });

  it("39 份样例都在", () => {
    expect(SAMPLE_IDS).toHaveLength(39);
  });

  it.each(SAMPLE_IDS)("样例 %s 按它声明的消息类型校验通过", (id) => {
    const s = sample(id);
    const result = checkMessage(s.message, s.payload);
    expect(result, `${s.file}: ${result.ok ? "" : result.errors.join("；")}`).toEqual({ ok: true });
  });

  it("多出的字段、改了类型的值都会被拒绝（校验只检查，不改数据）", () => {
    const draft = structuredClone(sample("02").payload) as Record<string, unknown>;
    expect(checkMessage("UserInputDraft", { ...draft, extra_field: 1 }).ok).toBe(false);
    const six = structuredClone(draft.six_dimensions) as { entries: Record<string, { value_text: unknown }> };
    six.entries.material!.value_text = 30;
    expect(checkMessage("UserInputDraft", { ...draft, six_dimensions: six }).ok).toBe(false);
    // 校验没有偷偷改动原数据
    expect((draft.six_dimensions as { entries: Record<string, { value_text: unknown }> }).entries.material!.value_text).toBe(
      "30",
    );
  });
});
