// 结果页共用：按运行意图读结果。键里带意图的 updated_at：恢复之后出了新结果、删除或过期，结果跟着重新读。
// 范围说明（scope notes）取这次运行绑定的那份运行前检查（意图里的 preflight，指纹要与结果引用的一致），不用当前能力或别的检查替代。
// 结果的显示信封（GET /display?kind=result）同时给出这份结果生成时绑定的显示资源版本：地图读地理清单时传它（交接单第 13.2 节）；
// 它为空就不传版本、也不拿当前版本冒充，页面写明“这份结果没有绑定的历史版本”。
import { useCapabilities, useDisplay, useIntent, useResult } from "../../api/queries";
import { useLocale } from "../../i18n/index";

export function useRunResult(intentId: string) {
  const locale = useLocale();
  const intent = useIntent(intentId);
  const caps = useCapabilities();
  const v = intent.data;
  const result = useResult(v?.run_id, v?.updated_at ?? "", !!v?.result_available);
  const display = useDisplay("result", v?.run_id, locale, v?.updated_at ?? "", !!v?.result_available && !!result.data);
  // 这次运行的意图信封（范围说明在意图的 preflight 里；它用这次运行预检查时绑定的版本）
  const intentDisplay = useDisplay("intent", intentId, locale, v?.updated_at ?? "", !!v);
  const boundPreflight =
    v?.preflight && result.data && v.preflight.preflight_fingerprint === result.data.preflight.preflight_fingerprint ? v.preflight : null;
  /** 这份结果绑定的显示资源版本：undefined = 还没读到信封；null = 信封读到了但没有绑定（或信封读不到）。 */
  const boundVersion: string | null | undefined = display.data ? (display.data.resource_version ?? null) : display.isError ? null : undefined;
  return { intent, caps, result, boundPreflight, display, intentDisplay, boundVersion };
}
