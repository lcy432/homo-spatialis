// 外框：顶部的标题、导航、语言与外观切换；正文；底部的会话说明。换页之后把焦点放到页面标题（从问题卡“去修改”过来时，
// 放到对应的字段）。语言与外观的切换只改显示，不保存、不提交任何东西。
import { type ReactNode, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, Outlet, useLocation } from "react-router";
import { useSession } from "../api/session";
import { formatTime } from "../i18n/format";
import { changeLocale, useLocale } from "../i18n/index";
import { useMode } from "../ui/mode";
import { STEP_SLUGS, type StepSlug } from "../draft/fieldSteps";
import styles from "./Layout.module.css";

function useFocusOnNavigate() {
  const location = useLocation();
  useEffect(() => {
    const field = (location.state as { focusField?: string } | null)?.focusField;
    const timer = window.setTimeout(() => {
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
    return () => window.clearTimeout(timer);
  }, [location.pathname, location.state]);
}

function Toggle<T extends string>({
  label,
  value,
  options,
  onChange,
  name,
}: {
  label: string;
  value: T;
  options: { value: T; text: string; lang?: string }[];
  onChange(v: T): void;
  name: string;
}) {
  return (
    <div className={styles.toggle} role="group" aria-label={label}>
      <span className={styles.toggleLabel} aria-hidden="true">
        {label}
      </span>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          lang={o.lang}
          data-toggle={`${name}-${o.value}`}
          aria-pressed={value === o.value}
          className={value === o.value ? styles.toggleOn : undefined}
          onClick={() => onChange(o.value)}
        >
          {o.text}
        </button>
      ))}
    </div>
  );
}

export function AppShell() {
  const { t } = useTranslation();
  const locale = useLocale();
  const { mode, setMode } = useMode();
  const { status, view } = useSession();
  useFocusOnNavigate();
  return (
    <>
      <a className="skip-link" href="#main">
        {t("app.skip")}
      </a>
      <header className={styles.header}>
        <div className={styles.headerInner}>
          <p className={styles.brand}>
            <Link to="/">{t("app.title")}</Link>
          </p>
          {status === "active" ? (
            <nav aria-label={t("app.nav.home")} className={styles.nav}>
              <NavLink to="/" end>
                {t("app.nav.home")}
              </NavLink>
              <NavLink to="/runs">{t("app.nav.runs")}</NavLink>
              <NavLink to="/settings">{t("app.nav.settings")}</NavLink>
            </nav>
          ) : null}
          <div className={styles.switches}>
            <Toggle
              name="lang"
              label={t("app.lang.label")}
              value={locale}
              onChange={changeLocale}
              options={[
                { value: "zh", text: "中文", lang: "zh-CN" },
                { value: "en", text: "English", lang: "en" },
              ]}
            />
            <Toggle
              name="mode"
              label={t("app.mode.label")}
              value={mode}
              onChange={setMode}
              options={[
                { value: "paper", text: t("app.mode.paper") },
                { value: "globe", text: t("app.mode.globe") },
              ]}
            />
          </div>
        </div>
      </header>
      <main id="main" className={styles.main}>
        <Outlet />
      </main>
      <footer className={styles.footer}>
        <div className={styles.footerInner}>
          <p className="small muted">{t("app.synthetic")}</p>
          {status === "active" && view ? (
            <p className="small muted">{t("session.expiresAt", { time: formatTime(view.session_expires_at, locale) })}</p>
          ) : null}
        </div>
      </footer>
    </>
  );
}

export function PageTitle({ children }: { children: ReactNode }) {
  return (
    <h1 id="page-title" tabIndex={-1}>
      {children}
    </h1>
  );
}

export function Stepper({ draftId, current }: { draftId: string; current: StepSlug }) {
  const { t } = useTranslation();
  return (
    <nav aria-label={t("steps.nav")}>
      <ol className={styles.stepper}>
        {STEP_SLUGS.map((s, i) => (
          <li key={s}>
            <NavLink to={`/draft/${encodeURIComponent(draftId)}/${s}`} aria-current={current === s ? "step" : undefined}>
              <span className={styles.stepNo}>{i + 1}</span> {t(`steps.${s}`)}
            </NavLink>
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function StepNav({ draftId, prev, next, extra }: { draftId: string; prev?: StepSlug; next?: StepSlug; extra?: ReactNode }) {
  const { t } = useTranslation();
  const href = (s: StepSlug) => `/draft/${encodeURIComponent(draftId)}/${s}`;
  return (
    <div className={styles.stepNav}>
      {prev ? (
        <Link className="button" to={href(prev)}>
          {t("common.prev")}
        </Link>
      ) : (
        <span />
      )}
      {extra}
      {next ? (
        <Link className="button primary" to={href(next)}>
          {t("common.next")}
        </Link>
      ) : null}
    </div>
  );
}
