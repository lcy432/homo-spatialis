// E3 的业务文字（*_zh、label、note、definition……）。界面不翻译它们：E3 还没有提供英文显示资源，所以英文界面里照样显示
// 中文原文，并标上 lang="zh-CN"（读屏按中文读）。整块内容的开头用 <BizNotice> 说一次“这一部分有中文原文”，不在每一行都贴提示。
// 以后 E3 提供显示资源时，只改这里的取值方式（按对象与字段的稳定标识取对应语言），页面不用改。
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useLocale } from "./index";

/** 一段 E3 原文。null / undefined 不显示（不补“0”或占位）。 */
export function Zh({ children, as: Tag = "span" }: { children: ReactNode; as?: "span" | "p" | "li" | "div" }) {
  const locale = useLocale();
  if (children === null || children === undefined || children === "") return null;
  return locale === "en" ? (
    <Tag lang="zh-CN" className="biz-zh">
      {children}
    </Tag>
  ) : (
    <Tag>{children}</Tag>
  );
}

/** 整块的说明：英文界面里，这一块含有服务提供的中文原文。 */
export function BizNotice() {
  const locale = useLocale();
  const { t } = useTranslation();
  if (locale !== "en") return null;
  return (
    <p className="biz-notice small" role="note">
      {t("biz.notice")}
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
