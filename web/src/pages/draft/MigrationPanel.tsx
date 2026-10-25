// 旧问卷的草稿：先看服务给的迁移说明（版本、要清空的答案与原文、影响），用户同意后 POST /drafts/{id}/migrate，
// 再进入编辑器。只隐藏控件而仍提交旧值是不允许的，所以不迁移就不进编辑器。
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { hasCode } from "../../api/errors";
import { useSession } from "../../api/session";
import type { DraftView } from "../../contract/types";
import { ApiErrorView, Section } from "../../components/common";
import { PageTitle } from "../../components/Layout";
import { BizNotice, Zh, ZhList } from "../../i18n/biz";

export function MigrationPanel({ view, onMigrated, onReload }: { view: DraftView; onMigrated(v: DraftView): void; onReload(): void }) {
  const { t } = useTranslation();
  const { api } = useSession();
  const m = view.migration;
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (!m) return null;
  const needConsent = m.consent_required && (m.removed?.length ?? 0) > 0;
  const migrate = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      onMigrated(await api.migrateDraft(view.draft.draft_id, { expected_revision: view.draft.revision, clear_removed: needConsent ? consent : false }));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const conflict = hasCode(error, "revision_conflict") || hasCode(error, "migration_not_needed");
  return (
    <div data-migration={m.from_questionnaire_version}>
      <PageTitle>{t("migration.title")}</PageTitle>
      <BizNotice />
      <p className="box warn">
        <Zh>{m.message_zh}</Zh>
      </p>
      <p className="small muted">{t("migration.versions", { from: m.from_questionnaire_version, to: m.to_questionnaire_version })}</p>
      <ZhList items={m.effects_zh} />
      <Section title={t("migration.removedTitle")}>
        {m.removed?.length ? (
          <ul>
            {m.removed.map((r) => (
              <li key={r.ref} data-removed={r.ref}>
                <Zh>{r.summary}</Zh>
                {r.raw_text ? (
                  <span className="small muted">
                    {" "}
                    {t("migration.original")}“{r.raw_text}”
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">{t("migration.noRemoved")}</p>
        )}
      </Section>
      {needConsent ? (
        <label className="choice top">
          <input type="checkbox" name="migration.consent" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          {t("migration.consent")}
        </label>
      ) : null}
      <ApiErrorView error={error} />
      {conflict ? (
        <p>
          <button type="button" onClick={onReload}>
            {t("migration.reload")}
          </button>
        </p>
      ) : null}
      <button type="button" className="primary" disabled={busy || (needConsent && !consent)} onClick={() => void migrate()} data-action="migrate">
        {busy ? t("migration.migrating") : t("migration.migrate")}
      </button>
    </div>
  );
}
