// 会话相关的页面：专属链接换会话（/invite）、需要会话的页面的门、没有会话时的说明。
import { type ReactNode, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { takePendingInvite, useSession } from "../api/session";
import { ApiErrorView, Loading } from "../components/common";
import { PageTitle } from "../components/Layout";

export function InvitePage() {
  const { t } = useTranslation();
  const { exchange, exchangeError } = useSession();
  const navigate = useNavigate();
  const started = useRef(false);
  const [noToken, setNoToken] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const token = takePendingInvite();
    if (!token) {
      setNoToken(true);
      return;
    }
    void exchange(token).then((ok) => {
      if (ok) navigate("/", { replace: true });
      else setFailed(true);
    });
  }, [exchange, navigate]);

  return (
    <div>
      <PageTitle>{t("session.needLinkTitle")}</PageTitle>
      {noToken ? <p className="box warn">{t("session.noToken")}</p> : null}
      {failed ? (
        <>
          <p className="box error">{t("session.exchangeFailed")}</p>
          <ApiErrorView error={exchangeError} />
        </>
      ) : null}
      {!noToken && !failed ? <Loading label={t("session.exchanging")} /> : null}
    </div>
  );
}

function NeedLink() {
  const { t } = useTranslation();
  const { status, retryBoot } = useSession();
  if (status === "unreachable") {
    return (
      <div>
        <PageTitle>{t("session.needLinkTitle")}</PageTitle>
        <div className="box error" role="alert">
          <p>{t("errors.network")}</p>
          <button type="button" onClick={retryBoot}>
            {t("common.retry")}
          </button>
        </div>
      </div>
    );
  }
  const title = status === "expired" ? t("session.expiredTitle") : t("session.needLinkTitle");
  const body =
    status === "expired" ? t("session.expiredBody") : status === "signed_out" ? t("session.signedOut") : t("session.needLinkBody");
  return (
    <div data-session={status}>
      <PageTitle>{title}</PageTitle>
      <p className={status === "expired" ? "box warn" : "box note"}>{body}</p>
    </div>
  );
}

/** 需要会话的页面：会话没准备好之前不渲染任何私有内容。 */
export function RequireSession({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { status } = useSession();
  if (status === "loading") return <Loading label={t("session.loading")} />;
  if (status !== "active") return <NeedLink />;
  return <>{children}</>;
}
