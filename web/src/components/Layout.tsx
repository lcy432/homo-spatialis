import { type ReactNode, useEffect } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";
import { useApiContext } from "../api/context";
import { T } from "../copy/zh";
import { useDraft } from "../draft/DraftContext";
import { useFlow } from "../flow/FlowContext";
import styles from "./Layout.module.css";

/** 换页之后把焦点放到页面标题；从问题卡“去修改”过来时，把焦点放到对应的字段。 */
function useFocusOnNavigate() {
  const location = useLocation();
  useEffect(() => {
    const field = (location.state as { focusField?: string } | null)?.focusField;
    const t = window.setTimeout(() => {
      if (field) {
        const name = field.replace(".entries.", ".");
        const el =
          document.querySelector<HTMLElement>(`[name="${CSS.escape(name)}"]`) ??
          document.querySelector<HTMLElement>(`[data-field="${CSS.escape(field.split(".")[0] ?? "")}"]`);
        if (el) {
          el.focus();
          return;
        }
      }
      document.getElementById("page-title")?.focus();
    }, 0);
    return () => window.clearTimeout(t);
  }, [location.pathname, location.state]);
}

export function AppShell() {
  const { scenario } = useApiContext();
  const { caps, versionMismatch, clearDraft } = useDraft();
  const { resetFlow } = useFlow();
  useFocusOnNavigate();
  const synthetic =
    caps.meta.data_pack.status === "synthetic_fixture_only" || caps.cities.some((c) => c.data_status === "synthetic_fixture");
  return (
    <>
      <header className={styles.header}>
        <div className={styles.headerInner}>
          <div>
            <p className={styles.brand}>{T.appTitle}</p>
            <p className={styles.brandSub}>{T.appSubtitle}</p>
          </div>
          <p className={styles.scenario}>
            样例：{scenario.title}
            {import.meta.env.DEV ? (
              <>
                {" · "}
                <Link className="tap" to="/dev/samples">{T.devLink}</Link>
              </>
            ) : null}
          </p>
        </div>
        {synthetic ? (
          <div className={styles.banner} role="note">
            <div className={styles.bannerInner}>{T.sampleBanner}</div>
          </div>
        ) : null}
      </header>
      <main className={styles.main}>
        {versionMismatch ? (
          <div className="warn" role="alert">
            <p>{T.versionMismatch}</p>
            <button
              type="button"
              onClick={() => {
                clearDraft();
                resetFlow();
              }}
            >
              {T.versionMismatchAction}
            </button>
          </div>
        ) : null}
        <Outlet />
      </main>
      <footer className={styles.footer}>
        <div className={styles.footerInner}>
          <p className="small muted">{T.tabOnly}</p>
          <button
            type="button"
            onClick={() => {
              if (window.confirm(T.clearConfirm)) {
                clearDraft();
                resetFlow();
              }
            }}
          >
            {T.clearAnswers}
          </button>
        </div>
      </footer>
    </>
  );
}

export function Stepper({ current }: { current: number }) {
  return (
    <nav aria-label={T.stepNav}>
      <ol className={styles.stepper}>
        {T.steps.map((s, i) => (
          <li key={s}>
            <NavLink to={`/fill/${i + 1}`} aria-current={current === i + 1 ? "step" : undefined}>
              {i + 1} · {s}
            </NavLink>
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function StepNav({ prev, next, extra }: { prev?: string; next?: string; extra?: ReactNode }) {
  return (
    <div className={styles.nav}>
      {prev ? (
        <Link className="button" to={prev}>
          {T.prev}
        </Link>
      ) : (
        <span />
      )}
      {extra}
      {next ? (
        <Link className="button primary" to={next}>
          {T.next}
        </Link>
      ) : null}
    </div>
  );
}

export function PageTitle({ children }: { children: ReactNode }) {
  return (
    <h1 id="page-title" tabIndex={-1}>
      {children}
    </h1>
  );
}
