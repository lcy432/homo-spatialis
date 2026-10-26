// 首页：次数、未结束的模拟、草稿列表与服务规则。数字全部照服务的会话视图显示，浏览器不推算账本。
import { useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";
import { useSession } from "../api/session";
import { PageTitle } from "../components/Layout";
import { PendingStartNotice } from "../components/StartPanel";
import { Section } from "../components/common";
import { allPendingStarts } from "../draft/startRecord";
import { BizNotice, ZhList } from "../i18n/biz";
import { formatTime } from "../i18n/format";
import { useLocale } from "../i18n/index";

export function HomePage() {
  const { t } = useTranslation();
  const locale = useLocale();
  const { view, refresh } = useSession();
  const navigate = useNavigate();
  // 回到首页时重新读服务的会话视图：站内新建、保存或删除过草稿之后，列表与次数照服务现在的事实显示（独立核查 F2）
  useEffect(() => {
    void refresh();
  }, [refresh]);
  // 这台设备上没有收到回复的“确认并开始”（重发同一个键找回同一个意图）
  const pendingStarts = useMemo(() => allPendingStarts(), []);
  if (!view) return null;
  const q = view.quota;
  const r = view.rules;
  return (
    <div>
      <PageTitle>{t("home.title")}</PageTitle>
      <p className="box note" data-quota={`${q.total}/${q.used}/${q.reserved}/${q.available}`}>
        {t("home.quota", { total: q.total, used: q.used, reserved: q.reserved, available: q.available })}
        <br />
        <span className="small">{t("home.quotaNote")}</span>
      </p>
      {r.planner_kind === "mock" ? <p className="small muted">{t("app.mockPlanner")}</p> : null}

      <PendingStartNotice records={pendingStarts} onStarted={(id) => navigate(`/intents/${encodeURIComponent(id)}`)} />
      {view.open_intent_id ? (
        <Section title={t("home.openIntent")}>
          <Link className="button primary" to={`/intents/${encodeURIComponent(view.open_intent_id)}`}>
            {t("home.openIntentAction")}
          </Link>
        </Section>
      ) : null}

      <Section title={t("home.drafts")}>
        {view.drafts?.length ? (
          <ul className="plain stack">
            {view.drafts.map((d) => (
              <li key={d.draft_id} className="row">
                <span>
                  {t("home.draftLine", {
                    id: d.draft_id,
                    rev: d.revision,
                    updated: formatTime(d.updated_at, locale),
                    expires: formatTime(d.expires_at, locale),
                  })}
                </span>
                <Link className="button" to={`/draft/${encodeURIComponent(d.draft_id)}/place`}>
                  {t("home.continueDraft")}
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">{t("home.noDrafts")}</p>
        )}
        <p>
          <Link className="button primary" to="/start">
            {t("home.newDraft")}
          </Link>{" "}
          <Link className="button" to="/runs">
            {t("home.runs")}
          </Link>
        </p>
      </Section>

      <Section title={t("home.rules")}>
        <BizNotice />
        <p className="small">
          {t("home.retention", {
            draft: r.draft_retention_days,
            profile: r.profile_retention_days,
            intent: r.intent_retention_days,
            result: r.result_retention_days,
          })}
        </p>
        <ZhList items={r.notes_zh} pointer="/rules/notes_zh" />
      </Section>
    </div>
  );
}
