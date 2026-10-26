// E3 的业务文字（*_zh、label、note、definition……）。界面不翻译它们：英文界面里，<Zh pointer=…> 在显示附层的作用域
// （display/scope）里按字段位置取服务给的英文（并核对信封的 source 在那个位置就是这段原文）；找到就显示英文，没有
// （服务报缺译、这个位置没有字段、作用域里没有这个对象）就照中文原文显示，标上 lang="zh-CN" 并说明“未翻译”（读屏按中文读）。
// 中文界面直接显示原文。整块内容的开头用 <BizNotice> 说一次，按这一块实际显示了多少段原文来写。
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { DisplayText } from "../contract/types";
import { type LookedUp, at, resolveDisplayText, useLookup, useRegisterMissing, useScopeMissingCount } from "../display/scope";
import { useLocale } from "./index";

type Tag = "span" | "p" | "li" | "div" | "strong";

/** 英文界面里显示一段查找结果：译文直接显示；原文带中文标记与“未翻译”说明。 */
export function Looked({ r, as: Tag = "span" }: { r: LookedUp; as?: Tag }) {
  const { t } = useTranslation();
  if (r.provided) return <Tag>{r.text}</Tag>;
  return (
    <Tag lang="zh-CN" className="biz-zh" data-missing={r.reason ?? "no_scope"} title={t("biz.untranslatedTitle")}>
      {r.text}
      <span className="biz-missing" aria-hidden="true">
        {" "}
        {t("biz.untranslated")}
      </span>
    </Tag>
  );
}

/**
 * 一段 E3 原文。pointer 是它在业务对象里的位置（JSON Pointer，例如 /review/occupation/message_zh）；
 * 显示作用域按这个位置取译文。null / undefined / 空字符串不显示（不补“0”或占位）。
 */
export function Zh({ children, as: Tag = "span", pointer }: { children: string | null | undefined; as?: Tag; pointer: string }) {
  const locale = useLocale();
  const original = children ?? "";
  const r = useLookup(pointer, original);
  useRegisterMissing(r, pointer, locale === "en" && original !== "");
  if (original === "") return null;
  if (locale !== "en") return <Tag>{original}</Tag>;
  return <Looked r={r} as={Tag} />;
}

/** 显示附层里的一段双语文字（知悉项说明、作答状态名称与含义）。空就不显示。 */
export function DText({ text, as = "span" }: { text: DisplayText | null | undefined; as?: Tag }) {
  const locale = useLocale();
  const r = resolveDisplayText(text, locale);
  if (!r) return null;
  if (locale !== "en") {
    const Tag = as;
    return <Tag>{r.text}</Tag>;
  }
  return <Looked r={r} as={as} />;
}

/** 整块的说明：英文界面里，这一块实际显示了几段服务没有给英文的原文（一段都没有就不显示）。 */
export function BizNotice() {
  const locale = useLocale();
  const { t } = useTranslation();
  const cov = useScopeMissingCount();
  if (locale !== "en") return null;
  if (cov && cov.missing === 0) return null;
  return (
    <p className="biz-notice small" role="note" data-missing-count={cov?.missing ?? undefined}>
      {cov ? t("biz.noticePartial", { n: cov.missing }) : t("biz.notice")}
    </p>
  );
}

/** 一串 E3 原文（例如 limitations_zh）：pointer 是数组的位置，每一项在 `${pointer}/${i}`。空列表不显示。 */
export function ZhList({ items, pointer }: { items: readonly string[] | null | undefined; pointer: string }) {
  if (!items?.length) return null;
  return (
    <ul>
      {items.map((s, i) => (
        <Zh as="li" key={`${i}.${s}`} pointer={at(pointer, i)}>
          {s}
        </Zh>
      ))}
    </ul>
  );
}

/** 需要纯文本的地方（option、aria-label、title）用的小组件之外的函数版：见 display/scope 的 useText。 */
export type { ReactNode };
