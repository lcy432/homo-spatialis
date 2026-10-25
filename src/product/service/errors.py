"""统一的安全错误：代码 → HTTP 状态与“发生了什么—影响是什么—下一步怎么做”的固定中文。

错误里从不放用户输入、内部路径、堆栈、原始校验异常或秘密；需要维护者查的情况给一个支持编号（``sup-…``），原文只进服务端
诊断。访问别人的编号与不存在的编号返回同一个 ``not_found``，不泄露别人有没有这次运行。
"""

from __future__ import annotations

from common.product_contracts import ProductMeta, ServiceError
from product.display import t

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
    "cancel_not_allowed": (
        409,
        "这次运行已经开始过，不能按“开始前取消”撤回",
        "没有取消；已经用掉的 1 次机会不退，已有的进度与结果保持不变",
        "可以等它接着运行，或者按页面上的动作删除这次运行",
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
        "内容已经不能查看或使用；查看、刷新都不续期",
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
    "run_options_invalid": (
        409,
        "这次选的城市、每城分身数、年数或费用上限不在服务允许的范围里",
        "没有准备模拟，也没有使用次数（field 指出是哪一项）",
        "按服务规则里的默认值与允许范围重新选择；城市要在草稿里改，再重新审阅与确认",
    ),
    "run_settings_unavailable": (
        409,
        "这次运行保存的设置或确认画像副本缺失、损坏",
        "系统不会改用服务现在的默认设置继续，没有开始，也没有使用次数",
        "取消这次准备后重新准备；问题一直出现时请联系维护者并提供支持编号",
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


#: ``expired`` 按对象分开说（E3C 接口补齐）：草稿、审阅、确认画像、运行意图与结果的保存规则不同，不能都说“首次结束起”。
#: 具体天数在服务规则（``SessionView.rules``）里，这里不写死。没有写对象时用 ``ERRORS["expired"]`` 的通用说法。
EXPIRED_SUBJECTS: dict[str, tuple[str, str, str]] = {
    "draft": (
        "这份草稿已经超过保存期限",
        "草稿从最后一次编辑起按服务规则保存，查看不续期；内容已经不能读取、审阅或确认",
        "新建一份草稿重新填写",
    ),
    "review": (
        "这份审阅所属的草稿已经超过保存期限",
        "审阅跟着它的草稿一起到期，不能再用来确认画像",
        "新建一份草稿重新填写并审阅",
    ),
    "profile": (
        "这份确认画像已经超过可以准备新模拟的期限",
        "确认画像从首次确认起按服务规则可用，重复确认、查看都不续期；已经准备的模拟用自己的副本与期限，不受影响",
        "重新审阅并确认还在保存期内的草稿，或新建草稿",
    ),
    "intent": (
        "这次模拟的准备已经超过保存期限",
        "等待确认、检查没通过、暂停或排队的准备从最后一次有效操作起按服务规则保存，查看不续期；没有开始或继续，也没有使用次数",
        "需要的话重新准备一次模拟",
    ),
    "result": (
        "这次运行的结果已经超过保存期限",
        "结果从运行首次结束起按服务规则保存，查看不续期；内容已经不能查看",
        "需要的话重新准备一次模拟",
    ),
}


class ServiceFailure(Exception):  # noqa: N818 - 业务拒绝，不是程序错误
    """业务拒绝：只带公开代码与支持编号。``subject`` 只用于 ``expired``：说明到期的是哪一类内容。"""

    def __init__(
        self,
        code: str,
        *,
        support_ref: str | None = None,
        retry_after: int | None = None,
        field: str | None = None,
        subject: str | None = None,
    ) -> None:
        if code not in ERRORS:
            raise ValueError(f"没有登记的错误代码 {code!r}")
        if subject is not None and (code != "expired" or subject not in EXPIRED_SUBJECTS):
            raise ValueError(f"没有登记的到期对象 {subject!r}")
        super().__init__(code)
        self.code = code
        self.support_ref = support_ref
        self.retry_after = retry_after
        self.field = field
        self.subject = subject

    @property
    def status(self) -> int:
        return ERRORS[self.code][0]


def error_text(code: str, subject: str | None = None) -> tuple[int, str, str, str]:
    """（HTTP 状态，发生了什么，影响是什么，下一步怎么做）。``expired`` 写了对象就用那一类的说法。"""
    status, what, impact, step = ERRORS[code]
    if code == "expired" and subject in EXPIRED_SUBJECTS:
        what, impact, step = EXPIRED_SUBJECTS[subject]
    return status, what, impact, step


def error_body(
    code: str,
    *,
    meta: ProductMeta | None = None,
    support_ref: str | None = None,
    retry_after: int | None = None,
    field: str | None = None,
    subject: str | None = None,
) -> ServiceError:
    status, what, impact, step = error_text(code, subject)
    base = (
        f"error.expired.{subject}"
        if code == "expired" and subject in EXPIRED_SUBJECTS
        else f"error.{code}"
    )
    return ServiceError(
        meta=meta,
        status=status,
        code=code,  # type: ignore[arg-type]
        message_zh=t(f"{base}.message", what),
        impact_zh=t(f"{base}.impact", impact),
        next_action_zh=t(f"{base}.next", step),
        support_ref=support_ref,
        retry_after_seconds=retry_after,
        field=field,  # type: ignore[arg-type]
    )
