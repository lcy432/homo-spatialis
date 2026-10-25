// 结果页共用：按运行意图读结果。键里带意图的 updated_at：恢复之后出了新结果、删除或过期，结果跟着重新读。
// 范围说明（scope notes）取这次运行绑定的那份运行前检查（意图里的 preflight，指纹要与结果引用的一致），不用当前能力或别的检查替代。
import { useCapabilities, useIntent, useResult } from "../../api/queries";

export function useRunResult(intentId: string) {
  const intent = useIntent(intentId);
  const caps = useCapabilities();
  const v = intent.data;
  const result = useResult(v?.run_id, v?.updated_at ?? "", !!v?.result_available);
  const boundPreflight =
    v?.preflight && result.data && v.preflight.preflight_fingerprint === result.data.preflight.preflight_fingerprint ? v.preflight : null;
  return { intent, caps, result, boundPreflight };
}
