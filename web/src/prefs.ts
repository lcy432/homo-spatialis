// 本机偏好：只存非私密的界面设置（显示语言、视觉模式）。答案、会话、草稿与结果一律不进浏览器存储。
// 浏览器存储可能不可用（隐私窗口、禁用站点数据）：读写都包在 try 里，失败时按默认值工作。

export type Locale = "zh" | "en";
export type VisualMode = "paper" | "globe";

const KEYS = { locale: "hs.pref.locale", mode: "hs.pref.mode" } as const;

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // 存不下就只在本次打开期间有效
  }
}

/** 第一次进入按浏览器语言选（中文开头的用中文，其余用英文）；之后记住用户的选择。 */
export function initialLocale(): Locale {
  const saved = read(KEYS.locale);
  if (saved === "zh" || saved === "en") return saved;
  const langs = typeof navigator === "undefined" ? [] : [...(navigator.languages ?? []), navigator.language];
  return langs.some((l) => typeof l === "string" && l.toLowerCase().startsWith("zh")) ? "zh" : "en";
}

export function saveLocale(locale: Locale): void {
  write(KEYS.locale, locale);
}

/** 视觉模式第一次默认稿纸；只记住用户主动切换的选择。 */
export function initialMode(): VisualMode {
  return read(KEYS.mode) === "globe" ? "globe" : "paper";
}

export function saveMode(mode: VisualMode): void {
  write(KEYS.mode, mode);
}

/** E5-A 离线原型在本标签页存过的样例答案与流程（共享结构 v2）：不并入服务身份，启动时清掉。 */
export function dropLegacyTabStorage(): void {
  try {
    for (const key of Object.keys(window.sessionStorage)) {
      if (key.startsWith("e5a.")) window.sessionStorage.removeItem(key);
    }
  } catch {
    // 读不到就没有可清的
  }
}
