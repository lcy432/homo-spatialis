"""读取时的纯函数渲染：按绑定与冻结资源生成每个显示位置的文字，先核对中文、再给英文；做不到就照实报缺失与原因。

缺失原因：``no_binding``（这段文字生成时没有记下 key，或者是旧对象）、``translation_missing``（资源里没有这条英文）、
``translation_stale``（代码里的中文改了，资源里的英文还是旧的那一版）、``source_changed``（用绑定重算的中文与消息里的不同）、
``ambiguous``（一个位置有不止一个绑定，不知道是谁——不挑第一个）、``untranslated_param``（参数是没有译文的原文片段）、
``resources_unavailable``（这个对象记下的资源版本不存在）、``resources_invalid``（那一版资源校验没通过或读不出来，一段也不用）、
``binding_stale``（绑定是为另一版消息生成的）、``binding_unverified``（旧方法按中文相等配上的绑定，身份不可靠）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from product.display.catalog import DisplayResources
from product.display.text import (
    fill,
    has_cjk,
    is_user_text,
    iter_text,
    months_en,
    months_zh,
    pointer_pattern,
)


@dataclass(frozen=True)
class BindingSet:
    """一组绑定与它要用的资源（``None`` 表示记下的资源版本不存在或不能用）。``valid=False``：绑定是为另一版消息生成的；
    ``verified=False``：旧方法按中文配上的绑定；``invalid=True``：资源校验没通过或登记表读不出来。"""

    bindings: tuple[dict[str, Any], ...]
    resources: DisplayResources | None
    resource_version: str | None
    valid: bool = True
    verified: bool = True
    invalid: bool = False


class _Missing(Exception):  # noqa: N818 - 渲染里的控制信号
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _param(p: Mapping[str, Any], res: DisplayResources, locale: str, raw: bool = True) -> str:
    kind = p["t"]
    if kind == "ent":
        entry = res.entity(p["kind"], p["id"], p["field"])
        if entry is None:
            raise _Missing("translation_missing")
        if entry["zh"] != p["zh"]:
            raise _Missing("translation_stale")
        if locale == "zh":
            return p["zh"]
        if entry.get("en") is None:
            raise _Missing("translation_missing")
        return entry["en"]
    if kind == "months":
        months = tuple(p["v"])
        return months_zh(months) if locale == "zh" else months_en(months)
    if kind in ("num", "code"):
        return p["v"]
    if kind == "raw":
        # 原文片段（可能是用户自己填的值）：英文只在允许时照抄，而且不能含中文
        if locale == "zh" or (raw and not has_cjk(p["v"])):
            return p["v"]
        raise _Missing("untranslated_param")
    if kind == "join":
        sep = p["zh"] if locale == "zh" else p["en"]
        return sep.join(_param(x, res, locale, raw) for x in p["items"])
    if kind == "sub":
        return _template(p["key"], p["zh_sha"], p["params"], res, locale, raw)
    raise _Missing("no_binding")


def _template(
    key: str,
    sha: str,
    params: Mapping[str, Any],
    res: DisplayResources,
    locale: str,
    raw: bool = True,
) -> str:
    tpl = res.template(key)
    if tpl is None:
        raise _Missing("translation_missing")
    if tpl["zh_sha"] != sha:
        raise _Missing("translation_stale")
    text = tpl["zh"] if locale == "zh" else tpl.get("en")
    if text is None:
        raise _Missing("translation_missing")
    return fill(text, {k: _param(v, res, locale, raw) for k, v in params.items()})


def render_candidate(
    cand: Mapping[str, Any], res: DisplayResources, locale: str, raw: bool = True
) -> str:
    return _template(cand["key"], cand["zh_sha"], cand["params"], res, locale, raw)


def field_key(cand: Mapping[str, Any]) -> str:
    """显示位置的稳定 key：实体名称写成 ``entity.<kind>.<field>:<编号>``，其余就是模板 key。"""
    if cand["key"] == "entity.label":
        v = cand["params"]["v"]
        if v.get("t") == "ent":
            return f"entity.{v['kind']}.{v['field']}:{v['id']}"
    return cand["key"]


def _resolve(
    text: str, entry: Mapping[str, Any], bset: BindingSet, locale: str, raw: bool = True
) -> tuple[str, str | None, str | None]:
    """一个绑定位置：（key，文字，缺失原因）。"""
    cands = entry["candidates"]
    key = field_key(cands[0])
    if not bset.valid:
        return key, None, "binding_stale"
    if not bset.verified:
        return key, None, "binding_unverified"
    if bset.invalid:
        return key, None, "resources_invalid"
    if bset.resources is None:
        return key, None, "resources_unavailable"
    if len(cands) != 1:
        return key, None, "ambiguous"  # 不知道这个位置是谁：不挑第一个
    try:
        # 按绑定重算中文只是核对：对不上就不给英文
        if render_candidate(cands[0], bset.resources, "zh", raw) != text:
            return key, None, "source_changed"
        return key, render_candidate(cands[0], bset.resources, locale, raw), None
    except _Missing as exc:
        return key, None, exc.reason


def resolve_text(
    text: str, candidate: Mapping[str, Any] | None, bset: BindingSet, locale: str
) -> tuple[str | None, str | None, str | None]:
    """不在消息位置上的一段显示文字（知悉项、作答状态）：（key，这种语言的文字，缺失原因）。没有绑定是 ``no_binding``。"""
    if candidate is None:
        return None, None, "no_binding"
    return _resolve(text, {"candidates": [candidate]}, bset, locale)


def display_fields(
    source: Any,
    sets: Iterable[BindingSet],
    locale: str,
    *,
    user_text: tuple[str, ...] = (),
    raw_params: bool = True,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """``source`` 里每个应展示的文字位置（含中文、不是用户原文）一条 ``DisplayField``；返回（fields，status，覆盖统计）。

    ``zh``：原文就是中文，全部 provided（文字照原文）。``en``：同一个位置按绑定组的顺序逐个尝试，第一个核对通过的给出英文；都不行就
    照实报 missing 与（第一个）原因。没有绑定的位置报 ``no_binding``。``raw_params=False``（即时回复）：原文片段参数不照抄进英文，
    那个位置报 ``untranslated_param``——即时回复的译文里不放用户输入。"""
    index: dict[str, list[tuple[dict[str, Any], BindingSet]]] = {}
    for bset in sets:
        for entry in bset.bindings:
            index.setdefault(entry["pointer"], []).append((entry, bset))
    fields: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    provided = 0
    for pointer, text in iter_text(source):
        if not has_cjk(text) or is_user_text(pointer, user_text):
            continue
        hits = index.get(pointer, [])
        if locale == "zh":
            key = (
                field_key(hits[0][0]["candidates"][0])
                if hits
                else f"source:{pointer_pattern(pointer)}"
            )
            version = hits[0][1].resource_version if hits else None
            fields.append(_field(pointer, key, text, None, version))
            provided += 1
            continue
        key, out, reason, version = f"source:{pointer_pattern(pointer)}", None, "no_binding", None
        for n, (entry, bset) in enumerate(hits):
            k, o, r = _resolve(text, entry, bset, locale, raw_params)
            if o is not None:
                key, out, reason, version = k, o, None, bset.resource_version
                break
            if n == 0:
                key, reason, version = k, r, bset.resource_version
        fields.append(_field(pointer, key, out, reason, version))
        if out is not None:
            provided += 1
        else:
            reasons[reason or "no_binding"] = reasons.get(reason or "no_binding", 0) + 1
    total = len(fields)
    status = "complete" if provided == total else ("unavailable" if provided == 0 else "partial")
    return fields, status, {"displayable": total, "provided": provided, "missing": reasons}


def _field(
    pointer: str, key: str, text: str | None, reason: str | None, version: str | None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "pointer": pointer,
        "key": key,
        "status": "provided" if text is not None else "missing",
        "text": text,
        "source_locale": "zh",
    }
    if reason is not None:
        out["missing_reason"] = reason
    if version is not None:
        out["resource_version"] = version
    return out


def coverage(
    source: Any,
    rec: Any,
    res: DisplayResources,
    *,
    user_text: tuple[str, ...] = (),
    constants: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """开发与测试用：一份刚生成的消息在 ``res`` 下的英文覆盖——应展示、已提供、缺失（按原因与 JSON Pointer 模式）。"""
    from product.display.text import bind_json

    bset = BindingSet(
        tuple(bind_json(source, rec, user_text=user_text, constants=constants)),
        res,
        res.resource_version,
    )
    fields, status, stats = display_fields(source, [bset], "en", user_text=user_text)
    patterns: dict[str, dict[str, int]] = {}
    for f in fields:
        if f["status"] == "missing":
            pat = pointer_pattern(f["pointer"])
            patterns.setdefault(pat, {}).setdefault(f["missing_reason"], 0)
            patterns[pat][f["missing_reason"]] += 1
    return {**stats, "status": status, "missing_patterns": patterns}
