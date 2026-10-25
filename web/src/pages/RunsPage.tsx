// 我的运行（GET /runs，新的在前）：阶段、有效设置、时间（服务记录的时间）、结果与删除。
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useCapabilities, useKeys, useRuns } from "../api/queries";
import { useSession } from "../api/session";
import type { DeletionReceipt, RunSummary } from "../contract/types";
import { Dialog } from "../components/Dialog";
import { PageTitle } from "../components/Layout";
import { cityLabelOf } from "../components/RunViews";
import { ApiErrorView, Loading } from "../components/common";
import { BizNotice, Zh, ZhList } from "../i18n/biz";
import { formatTime } from "../i18n/format";
import { useLocale } from "../i18n/index";

export function RunsPage() {
  const { t } = useTranslation();
  const locale = useLocale();
  const runs = useRuns();
  const caps = useCapabilities();
  const { api, refresh } = useSession();
  const keys = useKeys();
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<RunSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [receipt, setReceipt] = useState<DeletionReceipt | null>(null);

  if (runs.isPending) return <Loading />;
  const runList = runs.data;
  if (!runList) return <ApiErrorView error={runs.error} onRetry={() => void runs.refetch()} />;
  const list = runList.runs ?? [];

  const doDelete = async () => {
    if (!target?.run_id || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.deleteRun(target.run_id);
      setReceipt(r);
      setTarget(null);
      // 删掉的内容立刻不再显示：清掉这次运行的意图与结果缓存，列表重新读
      queryClient.removeQueries({ queryKey: keys.intent(target.intent_id) });
      void queryClient.invalidateQueries({ queryKey: keys.runs });
      void refresh();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageTitle>{t("runs.title")}</PageTitle>
      <BizNotice />
      <p className="small">
        {t("home.quota", {
          total: runList.quota.total,
          used: runList.quota.used,
          reserved: runList.quota.reserved,
          available: runList.quota.available,
        })}
      </p>
      {receipt ? (
        <div className="box note" role="status">
          <p>{t("runs.deleting")}</p>
          <Zh as="p">{receipt.note_zh}</Zh>
          <ZhList items={receipt.kept_zh} />
        </div>
      ) : null}
      {list.length === 0 ? <p className="muted">{t("runs.empty")}</p> : null}
      <ul className="plain stack">
        {list.map((r) => (
          <li key={r.intent_id} className="card" data-run={r.run_id ?? r.intent_id} data-stage={r.stage}>
            <p>
              <strong>
                <Zh>{r.stage_zh}</Zh>
              </strong>{" "}
              <span className="small muted">{t("runs.created", { time: formatTime(r.created_at, locale) })}</span>
              {r.finished_at ? <span className="small muted"> · {t("runs.finished", { time: formatTime(r.finished_at, locale) })}</span> : null}
              {r.expires_at ? <span className="small muted"> · {t("runs.expires", { time: formatTime(r.expires_at, locale) })}</span> : null}
            </p>
            {r.settings ? (
              <p className="small">
                {r.settings.cities.map((c, i) => (
                  <span key={c}>
                    {i ? "、" : ""}
                    <Zh>{cityLabelOf(caps.data, c)}</Zh>
                  </span>
                ))}{" "}
                · {t("prepare.clones")} {r.settings.clones_per_city} · {t("prepare.years")} {r.settings.years} · {t("intent.maxCost")}{" "}
                {r.settings.max_cost ?? "—"} {r.settings.currency ?? ""}
              </p>
            ) : null}
            <div className="row">
              <Link className="button" to={`/intents/${encodeURIComponent(r.intent_id)}`}>
                {t("runs.open")}
              </Link>
              {r.result_available ? (
                <Link className="button primary" to={`/results/${encodeURIComponent(r.intent_id)}`}>
                  {t("runs.result")}
                </Link>
              ) : null}
              {r.run_id && !["deleting", "deleted", "expired"].includes(r.stage) ? (
                <button type="button" className="danger" onClick={() => setTarget(r)}>
                  {t("runs.delete")}
                </button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      <ApiErrorView error={error} />
      <Dialog open={target !== null} onClose={() => setTarget(null)} title={t("runs.deleteTitle")}>
        <p>{t("runs.deleteBody")}</p>
        <div className="row">
          <button type="button" className="danger primary" disabled={busy} onClick={() => void doDelete()}>
            {t("runs.delete")}
          </button>
          <button type="button" onClick={() => setTarget(null)}>
            {t("common.cancel")}
          </button>
        </div>
      </Dialog>
    </div>
  );
}
