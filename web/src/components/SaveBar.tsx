// 草稿保存状态与冲突处理。冲突时不替用户选：改用服务器的版本，或把本地内容存成新的一版。
import { useTranslation } from "react-i18next";
import { useDraftEditor } from "../draft/DraftEditor";
import { formatTime } from "../i18n/format";
import { useLocale } from "../i18n/index";
import { ApiErrorView } from "./common";

export function SaveBar() {
  const { t } = useTranslation();
  const locale = useLocale();
  const { save, retrySave, useServerVersion, keepMyVersion } = useDraftEditor();
  if (save.kind === "conflict") {
    return (
      <div className="box warn" role="alert" data-save="conflict">
        <p>
          <strong>{t("save.conflictTitle")}</strong>
        </p>
        <p>{t("save.conflictBody", { rev: save.server.draft.revision })}</p>
        <p className="small muted">
          {t("save.conflictServerSummary")}：{t("common.lastUpdated", { time: formatTime(save.server.updated_at, locale) })}
        </p>
        <div className="row">
          <button type="button" onClick={useServerVersion}>
            {t("save.conflictUseServer")}
          </button>
          <button type="button" className="primary" onClick={keepMyVersion}>
            {t("save.conflictKeepMine")}
          </button>
        </div>
      </div>
    );
  }
  let text: string;
  switch (save.kind) {
    case "saved":
      text = t("save.saved", { rev: save.revision });
      break;
    case "saving":
      text = t("save.saving");
      break;
    case "dirty":
      text = t("save.dirty");
      break;
    case "rate_limited":
      text = t("save.rateLimited", { s: save.seconds });
      break;
    case "error":
      text = t("save.error");
      break;
  }
  return (
    <div className="save-bar" data-save={save.kind}>
      <p className="small" role="status" aria-live="polite">
        {text}
      </p>
      {save.kind === "error" ? (
        <>
          <ApiErrorView error={save.error} />
          <button type="button" onClick={retrySave}>
            {t("save.retry")}
          </button>
        </>
      ) : null}
    </div>
  );
}
