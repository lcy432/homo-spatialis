// /draft/:draftId/:step —— 读服务器草稿与能力清单，挂上编辑器（换步骤时编辑器不重建，撤销记录与未保存的改动都在）。
// 只适配当前问卷：旧版本（meta 的问卷 / 共享结构版本不是当前的）的草稿不进编辑器，只提示新开一份；没有迁移对话框（所有者 2026-10-26 定）。
import { useTranslation } from "react-i18next";
import { Link, Navigate, useParams } from "react-router";
import { ApiErrorView, Loading } from "../../components/common";
import { PageTitle, Stepper } from "../../components/Layout";
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
  const dm = draft.data.draft.meta;
  const cm = caps.data.meta;
  if (dm.schema_version !== cm.schema_version || dm.questionnaire_version !== cm.questionnaire_version || dm.conversion_version !== cm.conversion_version) {
    return (
      <div data-old-draft={dm.questionnaire_version}>
        <PageTitle>{t("draft.oldTitle")}</PageTitle>
        <p className="box warn">{t("draft.oldBody", { from: dm.questionnaire_version, to: cm.questionnaire_version })}</p>
        <p>
          <Link className="button primary" to="/start">
            {t("home.newDraft")}
          </Link>{" "}
          <Link className="button" to="/">
            {t("app.nav.home")}
          </Link>
        </p>
      </div>
    );
  }
  return (
    <DraftEditorProvider key={`${draftId}.${draft.data.draft.meta.questionnaire_version}`} draftId={draftId} caps={caps.data} initial={draft.data}>
      <Stepper draftId={draftId} current={step as StepSlug} />
      <SaveBar />
      <StepBody step={step as StepSlug} />
    </DraftEditorProvider>
  );
}
