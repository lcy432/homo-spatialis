// 开局设置：显示语言、模型输入与输出语言、外观。点“开始填写”才新建服务器草稿（服务器分配编号与修订号，城市按规则预选）；
// 模型语言写进草稿（职业描述的语言），其余答案保持未答。这里不保存画像，也不启动模拟。
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useCapabilities } from "../api/queries";
import { useSession } from "../api/session";
import { ApiErrorView, Loading, Section } from "../components/common";
import { PageTitle } from "../components/Layout";
import { BizNotice, ZhList } from "../i18n/biz";
import { changeLocale, useLocale } from "../i18n/index";
import type { Locale } from "../prefs";
import { useMode } from "../ui/mode";

export function ModelLanguageChoice({
  value,
  onChange,
  languages,
  name,
}: {
  value: string;
  onChange(v: string): void;
  languages: readonly string[];
  name: string;
}) {
  const { t } = useTranslation();
  return (
    <fieldset data-field="occupation.language">
      <legend>{t("start.modelLang")}</legend>
      <p className="small muted">{t("start.modelLangHelp")}</p>
      {(["zh", "en"] as const)
        .filter((l) => languages.includes(l))
        .map((l) => (
          <label key={l} className="choice">
            <input type="radio" name={name} checked={value === l} onChange={() => onChange(l)} />
            <span lang={l === "zh" ? "zh-CN" : "en"}>{l === "zh" ? "中文" : "English"}</span>
          </label>
        ))}
      {value === "en" ? <p className="small box warn">{t("start.modelLangEnGap")}</p> : null}
    </fieldset>
  );
}

export function StartPage() {
  const { t } = useTranslation();
  const locale = useLocale();
  const { mode, setMode } = useMode();
  const { api, refresh } = useSession();
  const caps = useCapabilities();
  const navigate = useNavigate();
  const [modelLang, setModelLang] = useState<string>(locale);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (caps.isPending) return <Loading />;
  const c = caps.data;
  if (!c) return <ApiErrorView error={caps.error} onRetry={() => void caps.refetch()} />;

  const begin = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      let view = await api.createDraft();
      if ((view.draft.occupation.language ?? "zh") !== modelLang) {
        const draft = { ...view.draft, occupation: { ...view.draft.occupation, language: modelLang } };
        view = await api.saveDraft(view.draft.draft_id, { draft, expected_revision: view.draft.revision });
      }
      // 会话视图里的草稿列表要立刻包含这一份（站内回首页不用整页刷新）
      await refresh();
      navigate(`/draft/${encodeURIComponent(view.draft.draft_id)}/place`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageTitle>{t("start.title")}</PageTitle>
      <p>{t("start.intro")}</p>

      <fieldset>
        <legend>{t("start.displayLang")}</legend>
        <p className="small muted">{t("start.displayLangHelp")}</p>
        {(["zh", "en"] as Locale[]).map((l) => (
          <label key={l} className="choice">
            <input type="radio" name="display-lang" checked={locale === l} onChange={() => changeLocale(l)} />
            <span lang={l === "zh" ? "zh-CN" : "en"}>{l === "zh" ? "中文" : "English"}</span>
          </label>
        ))}
      </fieldset>

      <ModelLanguageChoice value={modelLang} onChange={setModelLang} languages={c.occupation.languages} name="model-lang" />

      <fieldset>
        <legend>{t("start.mode")}</legend>
        <p className="small muted">{t("start.modeHelp")}</p>
        <label className="choice">
          <input type="radio" name="visual-mode" checked={mode === "paper"} onChange={() => setMode("paper")} />
          {t("start.modePaper")}
        </label>
        <label className="choice">
          <input type="radio" name="visual-mode" checked={mode === "globe"} onChange={() => setMode("globe")} />
          {t("start.modeGlobe")}
        </label>
      </fieldset>

      <Section title={t("start.scopeTitle")}>
        <BizNotice />
        <ZhList items={c.start_scenario} pointer="/start_scenario" />
        <details>
          <summary>{t("start.unsupportedTitle")}</summary>
          <ZhList items={c.unsupported} pointer="/unsupported" />
          <ZhList items={c.limits} pointer="/limits" />
        </details>
      </Section>

      <ApiErrorView error={error} />
      <p>
        <button type="button" className="primary" onClick={() => void begin()} disabled={busy} aria-busy={busy || undefined}>
          {busy ? t("start.creating") : t("start.begin")}
        </button>
      </p>
    </div>
  );
}
