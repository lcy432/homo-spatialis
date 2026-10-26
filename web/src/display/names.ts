// 需要纯字符串的名称（放进界面句子、option、aria-label、标题）：按位置在显示作用域里取当前语言的文字。
// 城市名优先用这份结果 / 画像自己带的名称与位置（历史对象用自己绑定的译文），没有才按能力清单的位置。
import type { ProductCapabilities } from "../contract/types";
import { useLocale } from "../i18n/index";
import { capsPtr, resultPtr } from "./pointers";
import { lookupText, useDisplayScopes } from "./scope";

export function useTextOf(): (pointer: string, original: string) => string {
  const stack = useDisplayScopes();
  const locale = useLocale();
  return (pointer, original) => (locale === "en" ? lookupText(stack, pointer, original).text : original);
}

/** 城市名：结果里有这座城就用结果的位置，否则用能力清单的位置；都没有就是编号。 */
export function useCityName(
  caps: ProductCapabilities | null | undefined,
  cities?: readonly { city_ref: string; label: string }[] | null,
): (ref: string) => string {
  const text = useTextOf();
  return (ref) => {
    const inResult = cities?.find((c) => c.city_ref === ref);
    if (inResult) return text(resultPtr.city(cities, ref), inResult.label);
    const inCaps = caps?.cities.find((c) => c.city_ref === ref);
    if (inCaps) return text(capsPtr.city(caps, ref), inCaps.label);
    return ref;
  };
}
