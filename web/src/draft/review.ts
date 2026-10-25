// 审阅服务器上已保存的那一版（POST /drafts/{id}/review）。只在草稿已保存时请求：审阅绑定修订号，修订号变了就重新审阅。
// 同一修订号的审阅不会变，不重复请求；正在改动时继续显示上一份审阅，但标明它已经过时。
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { queryRetry, queryRetryDelay, useKeys } from "../api/queries";
import { useSession } from "../api/session";
import { useDraftEditor } from "./DraftEditor";

export function useDraftReview(enabled = true) {
  const { api, status } = useSession();
  const keys = useKeys();
  const { draftId, save } = useDraftEditor();
  const saved = save.kind === "saved";
  const query = useQuery({
    queryKey: keys.review(draftId, save.revision),
    queryFn: () => api.review(draftId),
    enabled: status === "active" && saved && enabled,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 10 * 60_000,
    placeholderData: keepPreviousData,
    retry: queryRetry,
    retryDelay: queryRetryDelay,
  });
  const reply = query.data;
  /** 这份审阅就是当前已保存内容的审阅（没有改动在路上）。 */
  const current = !!reply && saved && reply.reviewed_revision === save.revision && !query.isPlaceholderData;
  return { query, reply, review: reply?.review ?? null, current };
}
