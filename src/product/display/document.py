"""一份私有显示绑定的保存格式（随所属对象保存在服务库或这次运行的工作目录里）与读取时的核对。

``{display_schema_version, pack_fingerprint, resource_version, source_fingerprint, bindings}``：``source_fingerprint`` 是绑定所对应的那份原消息
（规范 JSON）的摘要。读取时原消息与它不同（对象被改写过、绑定是为另一版生成的）就整组作废，显示为 ``binding_stale``；记下的资源
版本不存在就是 ``resources_unavailable``——都不回退到当前版本。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from product.display.catalog import DISPLAY_SCHEMA_VERSION, current_version, resources
from product.display.render import BindingSet
from product.display.text import Recorder, bind_json, iter_text


def json_fingerprint(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def binding_document(
    source: Any,
    rec: Recorder,
    *,
    pack_fingerprint: str,
    user_text: tuple[str, ...] = (),
) -> dict[str, Any]:
    """生成时调用：把同一次生成里记下的文字对到 ``source``，记下当前登记的资源版本与 ``source`` 的摘要。"""
    return {
        "display_schema_version": DISPLAY_SCHEMA_VERSION,
        "pack_fingerprint": pack_fingerprint,
        "resource_version": current_version(pack_fingerprint),
        "source_fingerprint": json_fingerprint(source),
        "bindings": bind_json(source, rec, user_text=user_text),
    }


def binding_set(
    doc: dict[str, Any] | str | None, current: Any, *, prefix: str = ""
) -> BindingSet | None:
    """读取时调用：``current`` 是这份绑定所属的对象现在的 JSON；``prefix`` 是它在外层原消息里的位置（例如 ``/preflight``）。"""
    if doc is None:
        return None
    if isinstance(doc, str):
        doc = json.loads(doc)
    version = doc.get("resource_version")
    res = None if version is None else resources(doc["pack_fingerprint"], version)
    bindings = tuple(
        {**b, "pointer": prefix + b["pointer"]} if prefix else b for b in doc.get("bindings", ())
    )
    return BindingSet(
        bindings=bindings,
        resources=res,
        resource_version=version,
        valid=doc.get("source_fingerprint") == json_fingerprint(current),
    )


def adopt_copies(rec: Recorder, source: Any, doc: dict[str, Any] | str | None) -> int:
    """另一份消息（``source``）照抄进这次生成的文字，借用它保存的绑定（例如结果照抄确认过的预检查里的规划器说明与费用上限）。
    只借这次生成自己没有记下的那几段文字；那份绑定对应的不是现在这份 ``source`` 就一段也不借。读取时还会按绑定重新生成中文核对，
    对不上的照实报缺失。返回借了几段。"""
    if doc is None:
        return 0
    if isinstance(doc, str):
        doc = json.loads(doc)
    if doc.get("source_fingerprint") != json_fingerprint(source):
        return 0
    texts = dict(iter_text(source))
    adopted = 0
    for entry in doc.get("bindings", ()):
        text = texts.get(entry["pointer"])
        if text is None or text in rec.by_text:
            continue
        for cand in entry["candidates"]:
            rec.add(text, cand)
        adopted += 1
    return adopted


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
    source: Any, rec: Recorder, *, pack_fingerprint: str, user_text: tuple[str, ...] = ()
) -> BindingSet:
    """读取时当场生成的部分（服务视图、能力清单等纯函数）：用当前登记的资源版本。"""
    version = current_version(pack_fingerprint)
    res = None if version is None else resources(pack_fingerprint, version)
    return BindingSet(
        bindings=tuple(bind_json(source, rec, user_text=user_text)),
        resources=res,
        resource_version=version,
        valid=True,
    )
