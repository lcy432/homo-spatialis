// 经验题（第 2 步）：只在选中职业有 experience_question 时出现；“有相关经验”先是空表、按相关职业逐个填整年数；
// “没有相关经验”是 provided + 空表；单选的勾选状态跟着用户点的那个，不因为年数还没填就跳回“没有”。
import { fireEvent, render, screen } from "@testing-library/react";
import { type ReactNode, useReducer } from "react";
import { describe, expect, it } from "vitest";
import { samplePayload } from "../src/contract/samples";
import type { DraftView, InputCatalog, ProductCapabilities } from "../src/contract/types";
import { DraftEditorContext, type DraftEditorValue } from "../src/draft/DraftEditor";
import { occupationById } from "../src/draft/catalog";
import { type DraftState, draftReducer, initialDraftState } from "../src/draft/reducer";
import { ExperienceQuestion } from "../src/pages/draft/Steps";
import { ModeProvider } from "../src/ui/mode";

const caps = samplePayload<ProductCapabilities>("129");
const catalog = caps.input_catalog as InputCatalog;
const created = samplePayload<DraftView>("131");

let latest: DraftState | null = null;
function Harness({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(draftReducer, created.draft, initialDraftState);
  latest = state;
  const value: DraftEditorValue = {
    caps,
    draftId: created.draft.draft_id,
    base: created,
    state,
    dispatch,
    save: { kind: "saved", revision: created.draft.revision },
    flush: async () => ({ kind: "saved", revision: created.draft.revision }),
    retrySave: () => undefined,
    useServerVersion: () => undefined,
    keepMyVersion: () => undefined,
  };
  return (
    <ModeProvider initial="paper">
      <DraftEditorContext.Provider value={value}>{children}</DraftEditorContext.Provider>
    </ModeProvider>
  );
}

describe("经验题", () => {
  it("文员没有经验题：不渲染；餐厅厨师有：显示 E3 的题目与相关职业", () => {
    const clerk = occupationById(catalog, "occ.office_clerk");
    const cook = occupationById(catalog, "occ.soc_35_2014");
    expect(clerk?.experience_question).toBeNull();
    const { container, rerender } = render(
      <Harness>
        <ExperienceQuestion catalog={catalog} occupation={clerk as NonNullable<typeof clerk>} />
      </Harness>,
    );
    expect(container.querySelector('[data-field="experience"]')).toBeNull();
    rerender(
      <Harness>
        <ExperienceQuestion catalog={catalog} occupation={cook as NonNullable<typeof cook>} />
      </Harness>,
    );
    expect(screen.getByText(/至少做过 1 年/)).toBeInTheDocument();
  });

  it("点“有相关经验”：只打开年数框、不写答案；填 2 年 → entries；再点“没有相关经验”→ provided + 空表", () => {
    const cook = occupationById(catalog, "occ.soc_35_2014") as NonNullable<ReturnType<typeof occupationById>>;
    render(
      <Harness>
        <ExperienceQuestion catalog={catalog} occupation={cook} />
      </Harness>,
    );
    const has = screen.getByDisplayValue("has") as HTMLInputElement;
    const none = screen.getByDisplayValue("none") as HTMLInputElement;
    expect(has.checked).toBe(false);
    fireEvent.click(has);
    expect(has.checked).toBe(true);
    expect(none.checked).toBe(false);
    // 还没填年数：业务答案仍是未答，不是“没有”（E5-R1）
    expect(latest?.draft.experience?.state).toBe("unanswered");
    const years = document.querySelector('input[name="experience.occ.soc_35_3031"]') as HTMLInputElement;
    expect(years).not.toBeNull();
    fireEvent.change(years, { target: { value: "2" } });
    expect(latest?.draft.experience).toEqual({ state: "provided", entries: [{ concept_id: "occ.soc_35_3031", years: 2 }] });
    expect(has.checked).toBe(true);
    fireEvent.click(none);
    expect(none.checked).toBe(true);
    expect(latest?.draft.experience).toEqual({ state: "provided", entries: [] });
    expect(document.querySelector('input[name="experience.occ.soc_35_3031"]')).toBeNull();
    fireEvent.click(screen.getByDisplayValue("unsure"));
    expect(latest?.draft.experience).toEqual({ state: "unsure", entries: [] });
  });
});
