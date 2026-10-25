// /draft/:draftId/:step —— 读服务器草稿与能力清单，挂上编辑器（换步骤时编辑器不重建，撤销记录与未保存的改动都在）。
import { useTranslation } from "react-i18next";
import { Navigate, useParams } from "react-router";
import { ApiErrorView, Loading } from "../../components/common";
import { Stepper } from "../../components/Layout";
import { SaveBar } from "../../components/SaveBar";
import { DraftEditorProvider, useDraftLoad } from "../../draft/DraftEditor";
import { STEP_SLUGS, type StepSlug } from "../../draft/fieldSteps";
import { ReviewStep } from "./ReviewStep";
import { FactsStep, MoreStep, PlaceStep, PrioritiesStep } from "./Steps";

function StepBody({ step }: { step: StepSlug }) {
  switch (step) {
    case "place":
      return <PlaceStep />;
    case "facts":
      return <FactsStep />;
    case "priorities":
      return <PrioritiesStep />;
    case "more":
      return <MoreStep />;
    case "review":
      return <ReviewStep />;
  }
}

export function DraftRoute() {
  const { t } = useTranslation();
  const { draftId = "", step = "place" } = useParams();
  const { caps, draft } = useDraftLoad(draftId);
  if (!(STEP_SLUGS as readonly string[]).includes(step)) return <Navigate to={`/draft/${encodeURIComponent(draftId)}/place`} replace />;
  if (caps.error || draft.error) {
    return (
      <ApiErrorView
        error={caps.error ?? draft.error}
        onRetry={() => {
          void caps.refetch();
          void draft.refetch();
        }}
      />
    );
  }
  if (!caps.data || !draft.data) return <Loading label={t("common.loading")} />;
  return (
    <DraftEditorProvider key={draftId} draftId={draftId} caps={caps.data} initial={draft.data}>
      <Stepper draftId={draftId} current={step as StepSlug} />
      <SaveBar />
      <StepBody step={step as StepSlug} />
    </DraftEditorProvider>
  );
}
