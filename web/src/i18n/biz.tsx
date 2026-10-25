// E3 的业务文字（*_zh、label、note、definition……）。界面不翻译它们：英文界面里，<Zh> 在显示附层的作用域（display/scope）里按
// 原文找服务给的英文；找到就显示英文，没有（服务报缺译、同一原文译文不同、作用域里没有这段原文）就照中文原文显示，标上
// lang="zh-CN" 并说明“未翻译”（读屏按中文读）。中文界面直接显示原文。整块内容的开头用 <BizNotice> 说一次，不在每一行都贴提示。
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { DisplayText } from "../contract/types";
import { type LookedUp, resolveDisplayText, useLookup, useScopeCoverage } from "../display/scope";
import { useLocale } from "./index";

type Tag = "span" | "p" | "li" | "div" | "strong";

/** 英文界面里显示一段查找结果：译文直接显示；原文带中文标记与“未翻译”说明。 */
export function Looked({ r, as: Tag = "span" }: { r: LookedUp; as?: Tag }) {
  const { t } = useTranslation();
  if (r.provided) return <Tag>{r.text}</Tag>;
  return (
    <Tag lang="zh-CN" className="biz-zh" data-missing={r.reason ?? "no_field"} title={t("biz.untranslatedTitle")}>
      {r.text}
      <span className="biz-missing" aria-hidden="true">
        {" "}
        {t("biz.untranslated")}
      </span>
    </Tag>
  );
}

/** 一段 E3 原文。null / undefined 不显示（不补“0”或占位）。pointer 可选：知道它在业务对象里的位置时按位置找，更准确。 */
export function Zh({ children, as: Tag = "span", pointer }: { children: ReactNode; as?: Tag; pointer?: string }) {
  const locale = useLocale();
  const original = typeof children === "string" ? children : "";
  const r = useLookup(original, pointer);
  if (children === null || children === undefined || children === "") return null;
  if (locale !== "en") return <Tag>{children}</Tag>;
  if (typeof children !== "string") {
    return (
      <Tag lang="zh-CN" className="biz-zh">
        {children}
      </Tag>
    );
  }
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

/** 整块的说明：英文界面里，这一块含有服务提供的中文原文（作用域里的信封英文完整时不显示）。 */
export function BizNotice() {
  const locale = useLocale();
  const { t } = useTranslation();
  const cov = useScopeCoverage();
  if (locale !== "en") return null;
  if (cov && cov.missing === 0 && cov.status === "complete") return null;
  return (
    <p className="biz-notice small" role="note" data-missing-count={cov?.missing ?? undefined}>
      {cov ? t("biz.noticePartial", { n: cov.missing }) : t("biz.notice")}
    </p>
  );
}

/** 一串 E3 原文（例如 limitations_zh）。空列表不显示。 */
export function ZhList({ items }: { items: readonly string[] | null | undefined }) {
  if (!items?.length) return null;
  return (
    <ul>
      {items.map((s, i) => (
        <Zh as="li" key={`${i}.${s}`}>
          {s}
        </Zh>
      ))}
    </ul>
  );
}
