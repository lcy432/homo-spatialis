"""一份私有显示绑定的保存格式（随所属对象保存在服务库或这次运行的工作目录里）与读取时的核对。

``{display_schema_version, binding_method, pack_fingerprint, resource_version, source_fingerprint, bindings, acknowledgements?}``：
``bindings`` 是按位置与对象身份记下的（``product.display.text.bind_json``）；``source_fingerprint`` 是绑定所对应的那份原消息（规范 JSON）
的摘要。读取时原消息与它不同（对象被改写过、绑定是为另一版生成的）就整组作废，显示为 ``binding_stale``；记下的资源版本不存在就是
``resources_unavailable``，资源校验没通过就是 ``resources_invalid``——都不回退到当前版本。没有 ``binding_method`` 的旧绑定是按中文
相等配上的，身份不可靠：显示为 ``binding_unverified``，不猜。

显示资源坏了、登记表读不出来，都只让显示附层缺失，不让业务读取、保存或错误回复失败（校验本身照样严格：坏资源一段也不用）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from product.display.catalog import (
    DISPLAY_SCHEMA_VERSION,
    DisplayResources,
    current_version,
    resources,
)
from product.display.render import BindingSet
from product.display.text import Recorder, bind_json, iter_text

#: 绑定的方法：按 ``t()`` 返回的文字对象与它在消息里的位置（不按中文相等）
BINDING_METHOD = "object-identity-v1"
_CURRENT = object()


def json_fingerprint(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def current_or_unusable(pack_fingerprint: str) -> tuple[str | None, bool]:
    """当前登记的资源版本；登记表读不出来时是（``None``，不可用）。"""
    try:
        return current_version(pack_fingerprint), False
    except Exception:  # 登记表坏了只让显示缺失，不让业务失败
        return None, True


def load_or_unusable(
    pack_fingerprint: str, version: str | None
) -> tuple[DisplayResources | None, bool]:
    """按版本读冻结资源：（资源，校验没通过）。版本不存在是（``None``，False）；被改过、读不出来是（``None``，True）。"""
    if version is None:
        return None, False
    try:
        return resources(pack_fingerprint, version), False
    except Exception:  # 资源校验失败：这一版一段也不用，显示附层照实报缺失
        return None, True


def binding_document(
    source: Any,
    rec: Recorder,
    *,
    pack_fingerprint: str,
    user_text: tuple[str, ...] = (),
    resource_version: Any = _CURRENT,
    extra: tuple[dict[str, Any], ...] = (),
    acknowledgements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """生成时调用：把同一次生成里 ``t()`` 返回的文字对象按位置对到 ``source``，记下资源版本与 ``source`` 的摘要。

    ``resource_version`` 不给就用当前登记的版本；一次运行的进度与结果给它这次运行预检查时绑定的版本（同一次运行用同一版解释）。
    ``extra`` 是从另一份消息原样带过来的绑定（``carry_bindings``）；``acknowledgements`` 是审阅的知悉项说明。"""
    if resource_version is _CURRENT:
        resource_version, _ = current_or_unusable(pack_fingerprint)
    doc = {
        "display_schema_version": DISPLAY_SCHEMA_VERSION,
        "binding_method": BINDING_METHOD,
        "pack_fingerprint": pack_fingerprint,
        "resource_version": resource_version,
        "source_fingerprint": json_fingerprint(source),
        "bindings": [*bind_json(source, rec, user_text=user_text), *extra],
    }
    if acknowledgements is not None:
        doc["acknowledgements"] = acknowledgements
    return doc


def binding_set(
    doc: dict[str, Any] | str | None, current: Any, *, prefix: str = ""
) -> BindingSet | None:
    """读取时调用：``current`` 是这份绑定所属的对象现在的 JSON；``prefix`` 是它在外层原消息里的位置（例如 ``/preflight``）。"""
    if doc is None:
        return None
    if isinstance(doc, str):
        doc = json.loads(doc)
    version = doc.get("resource_version")
    res, invalid = load_or_unusable(doc["pack_fingerprint"], version)
    bindings = tuple(
        {**b, "pointer": prefix + b["pointer"]} if prefix else b for b in doc.get("bindings", ())
    )
    return BindingSet(
        bindings=bindings,
        resources=res,
        resource_version=version,
        valid=doc.get("source_fingerprint") == json_fingerprint(current),
        verified=doc.get("binding_method") == BINDING_METHOD,
        invalid=invalid,
    )


def doc_version(doc: dict[str, Any] | str | None) -> str | None:
    """一份保存的绑定记下的资源版本（没有绑定就是 ``None``）。"""
    if doc is None:
        return None
    if isinstance(doc, str):
        doc = json.loads(doc)
    return doc.get("resource_version")


def carry_bindings(
    doc: dict[str, Any] | str | None,
    source: Any,
    target: Any,
    pairs: tuple[tuple[str, str], ...],
) -> tuple[dict[str, Any], ...]:
    """另一份消息（``source``，绑定是 ``doc``）里的文字原样放进了这次生成的消息（``target``）：按明确写出的位置对
    （``source`` 里的位置 → ``target`` 里的位置）把它的绑定带过来。那份绑定不是为现在这份 ``source`` 生成的、不是按位置绑定的，
    或两处文字不一样，就不带（读取时那个位置报缺失）。"""
    if doc is None:
        return ()
    if isinstance(doc, str):
        doc = json.loads(doc)
    if doc.get("binding_method") != BINDING_METHOD or doc.get(
        "source_fingerprint"
    ) != json_fingerprint(source):
        return ()
    by_pointer = {b["pointer"]: b for b in doc.get("bindings", ())}
    have, want = dict(iter_text(source)), dict(iter_text(target))
    out = []
    for src, dst in pairs:
        entry = by_pointer.get(src)
        if entry is not None and have.get(src) is not None and have.get(src) == want.get(dst):
            out.append({**entry, "pointer": dst})
    return tuple(out)


def capture(fn: Any, /, *args: Any, **kwargs: Any) -> tuple[Any, Recorder]:
    """在记录器里调用一个纯函数（服务视图、说明、投影），返回（结果，记录）。"""
    from product.display.text import recording

    with recording() as rec:
        value = fn(*args, **kwargs)
    return value, rec


def explained(make: Any, /, *, pack_fingerprint: str, **kwargs: Any) -> tuple[str, str]:
    """生成一份公开说明（``PublicExplanation``）并同时记下它的显示绑定：返回（说明的 JSON，绑定的 JSON）。"""
    value, rec = capture(make, **kwargs)
    body = value.model_dump(mode="json")
    return json.dumps(body, ensure_ascii=False), json.dumps(
        binding_document(body, rec, pack_fingerprint=pack_fingerprint), ensure_ascii=False
    )


def view_set(
    source: Any,
    rec: Recorder,
    *,
    pack_fingerprint: str,
    user_text: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
) -> BindingSet:
    """读取时当场生成的部分（服务视图、能力清单等纯函数）：用当前登记的资源版本。只绑定这次当场生成的文字对象；``exclude`` 里的
    位置（内嵌的历史画像、预检查、结果）即使被扫到也不接管——它们只用自己保存的绑定。"""
    version, broken = current_or_unusable(pack_fingerprint)
    res, invalid = load_or_unusable(pack_fingerprint, version)
    bindings = tuple(
        b
        for b in bind_json(source, rec, user_text=user_text)
        if not any(b["pointer"] == p or b["pointer"].startswith(p + "/") for p in exclude)
    )
    return BindingSet(
        bindings=bindings,
        resources=res,
        resource_version=version,
        valid=True,
        invalid=broken or invalid,
    )
