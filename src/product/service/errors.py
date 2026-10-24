"""统一的安全错误：代码 → HTTP 状态与“发生了什么—影响是什么—下一步怎么做”的固定中文。

错误里从不放用户输入、内部路径、堆栈、原始校验异常或秘密；需要维护者查的情况给一个支持编号（``sup-…``），原文只进服务端
诊断。访问别人的编号与不存在的编号返回同一个 ``not_found``，不泄露别人有没有这次运行。
"""

from __future__ import annotations

from common.product_contracts import ProductMeta, ServiceError

#: 代码 → (HTTP 状态, 发生了什么, 影响是什么, 下一步怎么做)
ERRORS: dict[str, tuple[int, str, str, str]] = {
    "unauthenticated": (
        401,
        "没有有效的会话，或专属链接已经失效",
        "不能读取或修改这个空间里的任何内容",
        "重新打开你的专属链接；如果链接被重发过，请使用最新的那一个",
    ),
    "not_found": (
        404,
        "没有找到这项内容",
        "这个编号在你的空间里不存在（也可能已经过期或删除）",
        "回到“我的运行”或重新创建",
    ),
    "revision_conflict": (
        409,
        "这份草稿已经在别处（例如另一个标签页）保存了更新的版本",
        "这次保存没有写入，较新的答案没有被覆盖",
        "重新读取最新的草稿，在它的基础上修改后再保存",
    ),
    "preflight_stale": (
        409,
        "你确认的不是当前这一份运行前说明（条件或资料变了，或说明已经更新）",
        "没有开始模拟，也没有使用次数",
        "查看最新的运行前说明后再确认",
    ),
    "quota_exhausted": (
        409,
        "这个空间的 3 次模拟机会已经用完或都在使用中",
        "不能再开始新的模拟",
        "查看已有的运行结果；次数不会自动重置",
    ),
    "idempotency_conflict": (
        409,
        "这个请求编号已经用于另一份不同的请求",
        "这次请求没有被执行",
        "为新的请求生成新的请求编号；重发同一个请求时请保持内容不变",
    ),
    "intent_conflict": (
        409,
        "你已经有一次正在准备或进行中的模拟，条件与这次不同",
        "没有创建新的运行意图",
        "先完成、取消或删除那一次，再用新的条件开始",
    ),
    "not_resumable": (
        409,
        "这次运行现在不能由你恢复",
        "没有发出新的模型请求，也没有改变运行",
        "如果页面说在等维护者处理，你不需要重新填写；否则查看运行状态",
    ),
    "result_unavailable": (
        409,
        "这次运行现在没有可以查看的进度或结果",
        "还没有开始、已经取消、运行中或停下时没有结果；没有就不编一个",
        "查看这次运行的状态；结束后再来看结果",
    ),
    "deleting": (
        409,
        "这次运行正在删除",
        "内容已经不可查看，正在安全收尾",
        "稍后刷新；删除完成后不能恢复",
    ),
    "deleted": (
        410,
        "这项内容已经删除",
        "内容不能再查看或恢复；删除不会重置使用次数",
        "需要的话重新创建",
    ),
    "expired": (
        410,
        "这项内容已经超过保存期限",
        "内容已经不能查看（保存期从首次结束起 30 天，查看不续期）",
        "需要的话重新创建",
    ),
    "data_pack_unavailable": (
        503,
        "运行需要的城市资料包现在不可用或与记录的版本不同",
        "没有开始模拟，也没有使用次数",
        "稍后再试；维护者会核对资料包",
    ),
    "paid_calls_disabled": (
        409,
        "服务现在没有开启付费模型调用",
        "不能开始需要真实模型的模拟，系统不会悄悄换成离线规划器",
        "等服务开启后再试；没有使用次数",
    ),
    "run_scale_fixed": (
        409,
        "本服务固定比较全部六个都会区（每城 4 个分身、3 年）",
        "这份确认的城市范围与服务规则不同，不能开始",
        "在草稿里选择全部六个都会区，重新审阅并确认",
    ),
    "rate_limited": (
        429,
        "请求太频繁",
        "这次请求没有被执行",
        "稍等一分钟再试",
    ),
    "csrf_failed": (
        403,
        "请求缺少或带错了页面校验码",
        "这次修改没有被执行",
        "刷新页面后再试",
    ),
    "origin_rejected": (
        403,
        "请求不是从本站页面发出的",
        "这次修改没有被执行",
        "从本站页面重新操作",
    ),
    "payload_too_large": (
        413,
        "请求内容太大",
        "这次请求没有被执行",
        "缩短填写内容后再试",
    ),
    "invalid_request": (
        422,
        "请求的格式或内容不符合要求",
        "这次请求没有被执行",
        "检查页面上的填写；问题一直出现时请联系维护者并提供支持编号",
    ),
    "internal_error": (
        500,
        "服务出现了内部问题",
        "这次请求没有完成",
        "稍后再试；问题一直出现时请联系维护者并提供支持编号",
    ),
}


class ServiceFailure(Exception):  # noqa: N818 - 业务拒绝，不是程序错误
    """业务拒绝：只带公开代码与支持编号。"""

    def __init__(
        self, code: str, *, support_ref: str | None = None, retry_after: int | None = None
    ) -> None:
        if code not in ERRORS:
            raise ValueError(f"没有登记的错误代码 {code!r}")
        super().__init__(code)
        self.code = code
        self.support_ref = support_ref
        self.retry_after = retry_after

    @property
    def status(self) -> int:
        return ERRORS[self.code][0]


def error_body(
    code: str,
    *,
    meta: ProductMeta | None = None,
    support_ref: str | None = None,
    retry_after: int | None = None,
) -> ServiceError:
    status, what, impact, step = ERRORS[code]
    return ServiceError(
        meta=meta,
        status=status,
        code=code,  # type: ignore[arg-type]
        message_zh=what,
        impact_zh=impact,
        next_action_zh=step,
        support_ref=support_ref,
        retry_after_seconds=retry_after,
    )
