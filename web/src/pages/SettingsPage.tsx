// 设置与删除：语言与外观（与页面顶部相同）、删除我的全部内容、退出这个设备。
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../api/session";
import type { DeletionReceipt } from "../contract/types";
import { Dialog } from "../components/Dialog";
import { PageTitle } from "../components/Layout";
import { ApiErrorView, Section } from "../components/common";
import { Zh, ZhList } from "../i18n/biz";

export function SettingsPage() {
  const { t } = useTranslation();
  const { api, signOut, resetAfterDeleteAll } = useSession();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [receipt, setReceipt] = useState<DeletionReceipt | null>(null);

  const deleteAll = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.deleteAll();
      setOpen(false);
      setReceipt(r);
      // 旧内容全部作废：换代次、清缓存，再重新读会话（会话本身还在）
      await resetAfterDeleteAll();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageTitle>{t("settings.title")}</PageTitle>
      {receipt ? (
        <div className="box note" role="status" data-deletion={receipt.status}>
          <p>{t("settings.deleteAllDone")}</p>
          <Zh as="p">{receipt.note_zh}</Zh>
          {receipt.kept_zh?.length ? (
            <>
              <p className="small">{t("settings.kept")}</p>
              <ZhList items={receipt.kept_zh} />
            </>
          ) : null}
        </div>
      ) : null}
      <Section title={t("settings.deleteAllTitle")}>
        <p>{t("settings.deleteAllBody")}</p>
        <button type="button" className="danger" onClick={() => setOpen(true)}>
          {t("settings.deleteAll")}
        </button>
        <ApiErrorView error={error} />
      </Section>
      <Section title={t("settings.signOut")}>
        <p>{t("settings.signOutBody")}</p>
        <button type="button" onClick={() => void signOut()}>
          {t("settings.signOut")}
        </button>
      </Section>
      <Dialog open={open} onClose={() => setOpen(false)} title={t("settings.deleteAllConfirm")}>
        <p>{t("settings.deleteAllBody")}</p>
        <div className="row">
          <button type="button" className="danger primary" disabled={busy} onClick={() => void deleteAll()}>
            {t("settings.deleteAll")}
          </button>
          <button type="button" onClick={() => setOpen(false)}>
            {t("common.cancel")}
          </button>
        </div>
      </Dialog>
    </div>
  );
}
