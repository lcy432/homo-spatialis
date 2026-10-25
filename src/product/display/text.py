"""显示文字的生成与绑定：``t()`` 生成原来的中文并记下稳定 key 与安全参数；``bind_json`` 把记录对到消息里的 JSON Pointer。

参数都有类型（不接受任意对象或表达式）：

* ``Ent(kind, ident, zh)``：一个有稳定编号的实体（城市、职业、机会、片区……）的名称；中文是生成时的原文，英文按 ``kind/ident`` 查冻结资源，
  资源里存的中文与这里不同就算过时；
* ``Months(months)``：月份集合（中文 ``1–12 月、3 月``，英文 ``months 1–12, month 3``）；
* ``Num(value)`` / ``Code(value)``：数字、金额、代码、英文专名等各语言相同的文字（不能含中文，含中文就当作 ``Raw``）；
* ``Raw(value)``：没有对应译文的原文片段——中文照写，英文缺失（不把中文混进英文句子）；
* ``Join(items, zh_sep, en_sep)``：列表；``Sub(key, zh, params)``：嵌套模板。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

_CJK = re.compile(r"[㐀-鿿豈-﫿　-〿＀-￯]")
_SLOT = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


def zh_sha(template: str) -> str:
    """中文模板的摘要（资源里的译文对应哪一版中文）。"""
    return hashlib.sha256(template.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------- 参数


@dataclass(frozen=True)
class Ent:
    kind: str
    ident: str
    zh: str
    field: str = "label"


@dataclass(frozen=True)
class Months:
    months: tuple[int, ...]


@dataclass(frozen=True)
class Num:
    value: str


@dataclass(frozen=True)
class Code:
    value: str


@dataclass(frozen=True)
class Raw:
    value: str


@dataclass(frozen=True)
class Join:
    items: tuple[Any, ...]
    zh_sep: str = "、"
    en_sep: str = ", "


@dataclass(frozen=True)
class Sub:
    key: str
    zh: str
    params: tuple[tuple[str, Any], ...] = ()


def _coerce(value: Any) -> Any:
    if isinstance(value, Ent | Months | Num | Code | Raw | Join | Sub):
        return value
    if isinstance(value, bool):
        raise TypeError("显示参数不接受布尔值")
    if isinstance(value, int):
        return Num(str(value))
    if isinstance(value, str):
        return Raw(value) if has_cjk(value) else Code(value)
    raise TypeError(f"显示参数只接受有类型的值，不接受 {type(value).__name__}")


def months_zh(months: tuple[int, ...]) -> str:
    """与结果投影原来的写法完全相同：连续的写成 ``1–12 月``，单月 ``3 月``，用顿号连接；没有月份写“无”。"""
    return _months(months, "无", lambda a, b: f"{a}–{b} 月", lambda a: f"{a} 月", "、")


def months_en(months: tuple[int, ...]) -> str:
    return _months(months, "none", lambda a, b: f"months {a}–{b}", lambda a: f"month {a}", ", ")


def _months(months, empty, span, one, sep) -> str:
    if not months:
        return empty
    out: list[str] = []
    start = prev = months[0]
    for m in months[1:]:
        if m == prev + 1:
            prev = m
            continue
        out.append(span(start, prev) if start != prev else one(start))
        start = prev = m
    out.append(span(start, prev) if start != prev else one(start))
    return sep.join(out)


def fill(template: str, values: Mapping[str, str]) -> str:
    """只替换 ``{名字}``；``{{`` 与 ``}}`` 是字面的花括号。缺参数就报错（模板与参数对不上是程序错误）。"""
    parts = template.split("{{")
    out = []
    for part in parts:
        pieces = part.split("}}")
        out.append("}".join(_SLOT.sub(lambda m: values[m.group(1)], p) for p in pieces))
    return "{".join(out)


def slots(template: str) -> set[str]:
    return set(_SLOT.findall(template.replace("{{", "").replace("}}", "")))


def render_param_zh(p: Any) -> str:
    if isinstance(p, Ent):
        return p.zh
    if isinstance(p, Months):
        return months_zh(p.months)
    if isinstance(p, Num | Code | Raw):
        return p.value
    if isinstance(p, Join):
        return p.zh_sep.join(render_param_zh(x) for x in p.items)
    if isinstance(p, Sub):
        return fill(p.zh, {k: render_param_zh(v) for k, v in p.params})
    raise TypeError(type(p).__name__)


def to_json(p: Any) -> dict[str, Any]:
    if isinstance(p, Ent):
        return {"t": "ent", "kind": p.kind, "id": p.ident, "field": p.field, "zh": p.zh}
    if isinstance(p, Months):
        return {"t": "months", "v": list(p.months)}
    if isinstance(p, Num):
        return {"t": "num", "v": p.value}
    if isinstance(p, Code):
        return {"t": "code", "v": p.value}
    if isinstance(p, Raw):
        return {"t": "raw", "v": p.value}
    if isinstance(p, Join):
        return {"t": "join", "items": [to_json(x) for x in p.items], "zh": p.zh_sep, "en": p.en_sep}
    if isinstance(p, Sub):
        return {
            "t": "sub",
            "key": p.key,
            "zh_sha": zh_sha(p.zh),
            "zh": p.zh,
            "params": {k: to_json(v) for k, v in p.params},
        }
    raise TypeError(type(p).__name__)


# ---------------------------------------------------------------------- 记录与 t()


@dataclass
class Recorder:
    """一次生成里记下的文字：中文 → {规范写法: 候选绑定}。"""

    by_text: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)

    def add(self, zh: str, candidate: dict[str, Any]) -> None:
        key = json.dumps(candidate, sort_keys=True, ensure_ascii=False)
        self.by_text.setdefault(zh, {})[key] = candidate


_ACTIVE: ContextVar[tuple[Recorder, ...]] = ContextVar("display_recorders", default=())


@contextmanager
def recording() -> Iterator[Recorder]:
    """在这段代码里生成的显示文字都记下来（嵌套时内外两层都记）。只影响同一个线程 / 上下文里的调用。"""
    rec = Recorder()
    token = _ACTIVE.set((*_ACTIVE.get(), rec))
    try:
        yield rec
    finally:
        _ACTIVE.reset(token)


def t(key: str, zh_template: str, /, **params: Any) -> str:
    """生成一段显示文字：返回按中文模板生成的中文（与原来的写法逐字相同），并在当前的记录器里记下 key 与有类型的参数。没有记录器时
    只返回中文——生产代码、脚本与测试的输出不受影响。"""
    typed = {k: _coerce(v) for k, v in params.items()}
    missing = slots(zh_template) - set(typed)
    if missing:
        raise ValueError(f"显示模板 {key} 缺参数 {sorted(missing)}")
    zh = fill(zh_template, {k: render_param_zh(v) for k, v in typed.items()})
    recorders = _ACTIVE.get()
    if recorders:
        candidate = {
            "key": key,
            "zh_sha": zh_sha(zh_template),
            "params": {k: to_json(v) for k, v in sorted(typed.items())},
        }
        for rec in recorders:
            rec.add(zh, candidate)
    return zh


def label(kind: str, ident: str, zh: str, field: str = "label") -> str:
    """一个实体的名称（中文照原文返回）。英文按 ``kind/ident/field`` 查冻结资源。"""
    return t("entity.label", "{v}", v=Ent(kind, ident, zh, field))


def say(s: Sub) -> str:
    """一条写成 ``Sub`` 常量的固定文字（表格里的一项，或既要单独出现、又要拼进别的句子的一句）：返回中文，并在当前的记录器里
    记下同一个 key 与参数。"""
    return t(s.key, s.zh, **dict(s.params))


# ---------------------------------------------------------------------- 绑定到 JSON Pointer


def pointer_escape(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")


def pointer_pattern(pointer: str) -> str:
    """把数组下标换成 ``*``（用户原文清单与覆盖报告按模式写）。"""
    return "/".join("*" if p.isdigit() else p for p in pointer.split("/"))


def iter_text(value: Any, base: str = "") -> Iterator[tuple[str, str]]:
    """消息里的每一个字符串叶子：（JSON Pointer，文字）。``sample`` 是样例出处标签，不是业务显示内容。"""
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "sample":
                continue
            yield from iter_text(v, f"{base}/{pointer_escape(str(k))}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from iter_text(v, f"{base}/{i}")
    elif isinstance(value, str):
        yield base, value


def is_user_text(pointer: str, patterns: tuple[str, ...]) -> bool:
    pat = pointer_pattern(pointer)
    return any(pat == p or pat.startswith(p + "/") for p in patterns)


def bind_json(
    source: Any, rec: Recorder, *, user_text: tuple[str, ...] = (), base: str = ""
) -> list[dict[str, Any]]:
    """把同一次生成里记下的文字对到 ``source`` 的 JSON Pointer（相对 ``source``，前面加 ``base``）。只对含中文的文字、不对用户原文。
    同一段中文对应几种不同的绑定时全部留下，渲染时只有它们给出相同的英文才算提供。"""
    out: list[dict[str, Any]] = []
    for pointer, text in iter_text(source):
        if not has_cjk(text) or is_user_text(pointer, user_text):
            continue
        cands = rec.by_text.get(text)
        if cands:
            out.append({"pointer": base + pointer, "candidates": list(cands.values())})
    return out
