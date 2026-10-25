"""独立的显示附层（E3 → E5 接口补齐，``e3-display-v1``）：切换中英文只是换一种解释方式，不是重新解释用户、生成另一份画像或重跑模拟。

* **原业务消息不变。** 生成中文的代码照旧生成同一份中文（内容身份、确认令牌都不变）；需要显示的文字改由 ``t(key, 中文模板, **参数)``
  生成：中文模板留在代码里（唯一来源），同时在当前的记录器里记下“这段中文 = 哪个稳定 key + 哪些有类型的安全参数”。
* **绑定。** 消息生成完之后，把记下的条目按 JSON Pointer 对到消息里的文字（``bind_json``）：只关联同一次生成里产生的文字，
  不拿任意中文字符串去反查英文。用户原文（自由文字、职业描述等）不绑定、不翻译，也不算进覆盖率的分母。
* **资源。** 英文模板与实体名称在 ``configs/product/display/``，按资料包与资源版本冻结在 ``data/display/<pack_fingerprint>/<resource_version>/``，
  不可覆盖；每份私有绑定记下它用的资源版本，读历史对象不偷偷换成最新译文。资源里同时存着每条英文对应的中文模板（的摘要），代码里的
  中文改了、译文还没跟上时显示为缺失（``translation_stale``），不显示过时的英文。
* **渲染。** 读取时只做纯函数渲染：按绑定与冻结资源生成英文，并先用同一份参数重算中文、与原消息逐字比较，不一致就报缺失，不猜。
  模板只允许 ``{名字}`` 占位与受控参数类型，不执行表达式、不返回 HTML。

这个包不依赖 ``product.service``；服务的显示出口在 ``product.service.display``。
"""

from product.display.text import (
    Code,
    Ent,
    Join,
    Months,
    Num,
    Raw,
    Recorder,
    Sub,
    bind_json,
    label,
    recording,
    say,
    t,
)

__all__ = [
    "Code",
    "Ent",
    "Join",
    "Months",
    "Num",
    "Raw",
    "Recorder",
    "Sub",
    "bind_json",
    "label",
    "recording",
    "say",
    "t",
]
