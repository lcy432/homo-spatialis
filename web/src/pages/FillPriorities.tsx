// 第 2 步：生活取舍。先分六维，物质生活为正数时再展开物质内部。
import { Allocator } from "../components/Allocator";
import { PageTitle, StepNav, Stepper } from "../components/Layout";
import { T } from "../copy/zh";
import { useDraft } from "../draft/DraftContext";
import { parsePoints } from "../draft/exact";

export function FillPriorities() {
  const { caps, state } = useDraft();
  const six = state.draft.six_dimensions;
  const parent = six.entries?.material;
  let materialStatus: "need_parent" | "zero" | "open";
  if (six.mode === "equal_split") materialStatus = "open";
  else if (!parent || parent.state !== "provided" || parent.value_text == null) materialStatus = "need_parent";
  else {
    const p = parsePoints(parent.value_text, caps.points.manual_max_decimals);
    materialStatus = !p.ok ? "need_parent" : p.value.n === 0n ? "zero" : "open";
  }
  return (
    <div>
      <Stepper current={2} />
      <PageTitle>2 · {T.steps[1]}</PageTitle>
      <Allocator group="six_dimensions" title={T.priorities} help={T.prioritiesHelp} />
      <section aria-label={T.materialTitle}>
        {materialStatus === "open" ? (
          <Allocator group="material" title={T.materialTitle} help={T.materialHelp} />
        ) : (
          <>
            <h2>{T.materialTitle}</h2>
            <p className="note">{materialStatus === "zero" ? T.materialNotApplicable : T.materialNeedParent}</p>
          </>
        )}
      </section>
      <StepNav prev="/fill/1" next="/fill/3" />
    </div>
  );
}
