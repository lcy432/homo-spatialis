// 旧问卷的草稿：先看服务给的迁移说明（版本、要清空的答案与原文、旧的百分比分配、影响），用户同意并选择之后
// POST /drafts/{id}/migrate，再进入编辑器。只隐藏控件而仍提交旧值是不允许的，所以不迁移就不进编辑器。
// * 同意只对“这份草稿、这个修订号、这次展示的清除清单与旧分配”有效（E5-R2）：任一变化（冲突重读、另一处又加了要清空的答案）
//   都清掉勾选与选择，让用户重新看过；提交仍带服务端的 expected_revision。
// * 旧的六维 / 物质分配是百分比，不是独立评分（DECISIONS/0047）：用户必须明确选“重新评分”或“同等重视”，页面不默认选、
//   不把旧百分比抄进新滑条；删除旧板块的同意也不能当作这个选择。
// * 文字按位置取这份草稿自己的显示信封（kind = draft；E5-R7），不只继承能力清单。
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { hasCode } from "../../api/errors";
import { useDisplay } from "../../api/queries";
import { useSession } from "../../api/session";
import type { DraftMigrateRequest, DraftView } from "../../contract/types";
import { ApiErrorView, Section } from "../../components/common";
import { PageTitle } from "../../components/Layout";
import { DisplayScope, at } from "../../display/scope";
import { BizNotice, Zh, ZhList } from "../../i18n/biz";
import { useLocale } from "../../i18n/index";

/** 这次展示的迁移内容的身份：草稿、修订、要清空的条目、旧分配。变了就是另一次同意。 */
function consentKey(view: DraftView): string {
  const m = view.migration;
  const removed = (m?.removed ?? []).map((r) => `${r.section}:${r.ref}:${r.summary}:${r.raw_text ?? ""}`).join("|");
  const allocations = (m?.allocations ?? [])
    .map((a) => `${a.group}:${a.mode ?? ""}:${(a.lines ?? []).map((l) => `${l.key}=${l.state}/${l.value_text ?? ""}`).join(",")}`)
    .join("|");
  return [view.draft.draft_id, view.draft.revision, m?.to_questionnaire_version ?? "", removed, allocations].join("#");
}

export function MigrationPanel({ view, onMigrated, onReload }: { view: DraftView; onMigrated(v: DraftView): void; onReload(): void }) {
  const key = consentKey(view);
  // 同意与选择随展示内容一起换掉（key 变了就是新的面板，旧勾选不会留下来）
  return <MigrationPanelBody key={key} view={view} onMigrated={onMigrated} onReload={onReload} />;
}

function MigrationPanelBody({ view, onMigrated, onReload }: { view: DraftView; onMigrated(v: DraftView): void; onReload(): void }) {
  const { t } = useTranslation();
  const locale = useLocale();
  const { api } = useSession();
  const m = view.migration;
  const [consent, setConsent] = useState(false);
  const [choice, setChoice] = useState<"rerate" | "equal_weight" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const display = useDisplay("draft", view.draft.draft_id, locale, String(view.draft.revision), true);
  if (!m) return null;
  const needConsent = m.consent_required && (m.removed?.length ?? 0) > 0;
  const needChoice = !!m.allocation_choice_required && (m.allocations?.length ?? 0) > 0;
  const ready = (!needConsent || consent) && (!needChoice || choice !== null);
  const migrate = async () => {
    if (busy || !ready) return;
    setBusy(true);
    setError(null);
    const req: DraftMigrateRequest = { expected_revision: view.draft.revision };
    if (needConsent) req.clear_removed = consent;
    if (needChoice && choice) req.allocation_choice = choice;
    try {
      onMigrated(await api.migrateDraft(view.draft.draft_id, req));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const conflict = hasCode(error, "revision_conflict") || hasCode(error, "migration_not_needed");
  const M = "/migration";
  return (
    <DisplayScope envelope={display.data}>
      <div data-migration={m.from_questionnaire_version} data-migration-key={view.draft.revision}>
        <PageTitle>{t("migration.title")}</PageTitle>
        <BizNotice />
        <p className="box warn">
          <Zh pointer={`${M}/message_zh`}>{m.message_zh}</Zh>
        </p>
        <p className="small muted">{t("migration.versions", { from: m.from_questionnaire_version, to: m.to_questionnaire_version })}</p>
        <ZhList items={m.effects_zh} pointer={`${M}/effects_zh`} />

        <Section title={t("migration.removedTitle")}>
          {m.removed?.length ? (
            <ul>
              {m.removed.map((r, i) => (
                <li key={r.ref} data-removed={r.ref}>
                  <Zh pointer={at(`${M}/removed`, i, "summary")}>{r.summary}</Zh>
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
          {needConsent ? (
            <label className="choice top">
              <input type="checkbox" name="migration.consent" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              {t("migration.consent")}
            </label>
          ) : null}
        </Section>

        {m.allocations?.length ? (
          <Section title={t("migration.allocationsTitle")}>
            <p className="small">{t("migration.allocationsNote")}</p>
            {m.allocations.map((a, ai) => (
              <div key={a.group} data-legacy-allocation={a.group}>
                <h3>
                  {a.group === "six_dimensions" ? t("review.six") : t("review.material")}
                  <span className="small muted"> · {a.mode === "equal_split" ? t("migration.modeEqual") : t("migration.modeManual")}</span>
                </h3>
                <ul className="small">
                  {(a.lines ?? []).map((l, li) => (
                    <li key={l.key}>
                      <Zh pointer={at(`${M}/allocations`, ai, "lines", String(li), "label")}>{l.label}</Zh>：
                      {l.state === "provided" ? (l.value_text ?? "") : t(`states.${l.state}` as "states.unanswered")}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            {needChoice ? (
              <fieldset data-field="allocation_choice">
                <legend>{t("migration.choiceLegend")}</legend>
                <label className="choice top">
                  <input type="radio" name="migration.allocation_choice" value="rerate" checked={choice === "rerate"} onChange={() => setChoice("rerate")} />
                  <span>
                    <strong>{t("migration.choiceRerate")}</strong>
                    <span className="small muted" style={{ display: "block" }}>
                      {t("migration.choiceRerateHelp")}
                    </span>
                  </span>
                </label>
                <label className="choice top">
                  <input type="radio" name="migration.allocation_choice" value="equal_weight" checked={choice === "equal_weight"} onChange={() => setChoice("equal_weight")} />
                  <span>
                    <strong>{t("migration.choiceEqual")}</strong>
                    <span className="small muted" style={{ display: "block" }}>
                      {t("migration.choiceEqualHelp")}
                    </span>
                  </span>
                </label>
              </fieldset>
            ) : null}
          </Section>
        ) : null}

        <ApiErrorView error={error} />
        {conflict ? (
          <p>
            <button type="button" onClick={onReload} data-action="reload-draft">
              {t("migration.reload")}
            </button>
          </p>
        ) : null}
        <button type="button" className="primary" disabled={busy || !ready} onClick={() => void migrate()} data-action="migrate">
          {busy ? t("migration.migrating") : t("migration.migrate")}
        </button>
        {!ready ? <p className="small muted">{t("migration.notReady")}</p> : null}
      </div>
    </DisplayScope>
  );
}
