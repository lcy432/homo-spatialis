// 双语框架：i18next 管界面自己的文字（zh.ts / en.ts）。E3 的业务文字不经过这里（见 biz.tsx）。
// 语言切换只改显示：不改答案原文、职业描述的语言、偏好、币种、结果或任何指纹。
import i18n from "i18next";
import { initReactI18next, useTranslation } from "react-i18next";
import { type Locale, initialLocale, saveLocale } from "../prefs";
import { en } from "./en";
import { type Dict, zh } from "./zh";

declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: "translation";
    resources: { translation: Dict };
  }
}

export function htmlLang(locale: Locale): string {
  return locale === "zh" ? "zh-CN" : "en";
}

/** 页面的 lang 与浏览器标签页的标题跟着显示语言。 */
function applyDocumentLang(locale: Locale): void {
  if (typeof document === "undefined") return;
  document.documentElement.lang = htmlLang(locale);
  document.title = (locale === "en" ? en : zh).app.title;
}

export function setupI18n(locale: Locale = initialLocale()): typeof i18n {
  if (!i18n.isInitialized) {
    void i18n.use(initReactI18next).init({
      resources: { zh: { translation: zh }, en: { translation: en } },
      lng: locale,
      fallbackLng: false,
      interpolation: { escapeValue: false },
      returnNull: false,
      initAsync: false,
    });
    i18n.on("languageChanged", (lng) => applyDocumentLang(lng === "en" ? "en" : "zh"));
  } else if (i18n.language !== locale) {
    void i18n.changeLanguage(locale);
  }
  applyDocumentLang(locale);
  return i18n;
}

export function currentLocale(): Locale {
  return i18n.language === "en" ? "en" : "zh";
}

/** 用户主动切换语言：记住选择。 */
export function changeLocale(locale: Locale): void {
  saveLocale(locale);
  void i18n.changeLanguage(locale);
}

export function useLocale(): Locale {
  const { i18n: inst } = useTranslation();
  return inst.language === "en" ? "en" : "zh";
}

export { i18n };
