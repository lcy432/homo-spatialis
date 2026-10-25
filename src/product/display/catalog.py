"""显示资源：可审阅的源文件（``configs/product/display/``）→ 按资料包与资源版本冻结（``data/display/<pack_fingerprint>/<resource_version>/``）。

* 模板：``templates/*.yaml``，``{templates: {key: {zh: 中文模板, en: 英文模板或 null}}}``。中文是**对齐用的副本**：代码里的中文模板才是
  唯一来源；冻结时记下它的摘要，读取时绑定里的摘要与它不同就说明译文过时，显示为缺失。
* 实体：``entities/*.yaml`` 与生成的 ``entities/*.json``，``{entities: {kind: {ident: {field: {zh, en}}}}}``；带 ``pack_id`` 的文件只用于那个
  资料包。英文专名（PUMA、县名、SOC 职业名）来自资料本身。
* 地理：``geography/<pack_id>.json``（见 ``product.display.geography``）。
* 冻结的资源版本由内容摘要决定，目录一旦写下就不能覆盖；``configs/product/display/registry.yaml`` 记每个资料包当前用哪个版本，旧版本
  保留，历史对象按它们记下的版本读取。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from common.product_contracts import DISPLAY_SCHEMA_VERSION
from product.display.text import has_cjk, slots, zh_sha

REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / "configs/product/display"
FROZEN = REPO / "data/display"
REGISTRY = SOURCE / "registry.yaml"
RESOURCE_PREFIX = "e3-display-res-v1-"


class DisplayResourceError(ValueError):
    """显示资源不合法（模板占位对不上、英文里混着中文、冻结目录被改过……）。"""


@dataclass(frozen=True)
class DisplayResources:
    """一个资料包的一个冻结资源版本（只读）。"""

    pack_fingerprint: str
    resource_version: str
    templates: dict[str, dict[str, Any]]
    entities: dict[str, dict[str, dict[str, dict[str, Any]]]]
    geography: dict[str, Any] | None

    def template(self, key: str) -> dict[str, Any] | None:
        return self.templates.get(key)

    def entity(self, kind: str, ident: str, field: str) -> dict[str, Any] | None:
        return self.entities.get(kind, {}).get(ident, {}).get(field)


# ---------------------------------------------------------------------- 源文件


def _read(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)


def load_templates(root: Path = SOURCE) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "templates").glob("*.yaml")):
        for key, entry in (_read(path) or {}).get("templates", {}).items():
            if key in out:
                raise DisplayResourceError(f"模板 {key} 在两个文件里都有（{path.name}）")
            zh, en = entry.get("zh"), entry.get("en")
            if not isinstance(zh, str) or not zh:
                raise DisplayResourceError(f"模板 {key} 没有中文副本")
            if en is not None:
                if not isinstance(en, str) or not en.strip():
                    raise DisplayResourceError(f"模板 {key} 的英文是空的")
                if slots(en) != slots(zh):
                    raise DisplayResourceError(
                        f"模板 {key} 的中英文占位不同：{sorted(slots(zh))} / {sorted(slots(en))}"
                    )
                if has_cjk(en):
                    raise DisplayResourceError(f"模板 {key} 的英文里有中文")
                if "<" in en or ">" in en:
                    raise DisplayResourceError(f"模板 {key} 的英文里有尖括号（不返回 HTML）")
            out[key] = {"zh": zh, "zh_sha": zh_sha(zh), "en": en}
    return out


def load_entities(
    pack_id: str | None, root: Path = SOURCE
) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
    """共用的实体（文件里没有 ``pack_id``）加上这个资料包自己的实体。同一个实体字段出现两次就拒绝，不用后一条覆盖前一条。"""
    out: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    for path in sorted([*(root / "entities").glob("*.yaml"), *(root / "entities").glob("*.json")]):
        data = _read(path) or {}
        owner = data.get("pack_id")
        if owner is not None and owner != pack_id:
            continue
        for kind, items in data.get("entities", {}).items():
            for ident, fields in items.items():
                for fname, entry in fields.items():
                    slot = out.setdefault(kind, {}).setdefault(str(ident), {})
                    if fname in slot:
                        raise DisplayResourceError(
                            f"实体 {kind}/{ident}/{fname} 重复（{path.name}）"
                        )
                    zh, en = entry.get("zh"), entry.get("en")
                    if not isinstance(zh, str) or not zh:
                        raise DisplayResourceError(f"实体 {kind}/{ident}/{fname} 没有中文")
                    if en is not None and (
                        not isinstance(en, str) or not en.strip() or has_cjk(en)
                    ):
                        raise DisplayResourceError(f"实体 {kind}/{ident}/{fname} 的英文不合法")
                    slot[fname] = {"zh": zh, "en": en}
    return out


def load_geography(pack_id: str | None, root: Path = SOURCE) -> dict[str, Any] | None:
    path = root / "geography" / f"{pack_id}.json"
    return _read(path) if pack_id and path.is_file() else None


# ---------------------------------------------------------------------- 冻结与读取


def _canonical(content: Any) -> str:
    return json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def build_content(pack_fingerprint: str, pack_id: str, root: Path = SOURCE) -> dict[str, Any]:
    return {
        "display_schema_version": DISPLAY_SCHEMA_VERSION,
        "pack_fingerprint": pack_fingerprint,
        "pack_id": pack_id,
        "templates": load_templates(root),
        "entities": load_entities(pack_id, root),
        "geography": load_geography(pack_id, root),
    }


def version_of(content: dict[str, Any]) -> str:
    return RESOURCE_PREFIX + hashlib.sha256(_canonical(content).encode("utf-8")).hexdigest()[:16]


def freeze(
    pack_fingerprint: str, pack_id: str, *, root: Path | None = None, frozen: Path | None = None
) -> str:
    """把当前源文件冻结成这个资料包的一个资源版本（版本名由内容决定）；已经有同一个版本就核对内容相同，不覆盖。返回版本名。"""
    content = build_content(pack_fingerprint, pack_id, root or SOURCE)
    version = version_of(content)
    target = (frozen or FROZEN) / pack_fingerprint / version
    body = _canonical({**content, "resource_version": version})
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if target.exists():
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("sha256") != digest:
            raise DisplayResourceError(f"冻结的显示资源 {version} 已经存在而且内容不同：不覆盖")
        return version
    target.mkdir(parents=True)
    (target / "resources.json").write_text(body + "\n", encoding="utf-8", newline="\n")
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "resource_version": version,
                "pack_fingerprint": pack_fingerprint,
                "pack_id": pack_id,
                "sha256": digest,
            },
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return version


@lru_cache(maxsize=32)
def _load_frozen(frozen: str, pack_fingerprint: str, version: str) -> DisplayResources | None:
    target = Path(frozen) / pack_fingerprint / version
    if not (target / "resources.json").is_file():
        return None
    body = (target / "resources.json").read_text(encoding="utf-8").rstrip("\n")
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != manifest.get("sha256"):
        raise DisplayResourceError(f"冻结的显示资源 {version} 被改过")
    data = json.loads(body)
    if data.get("pack_fingerprint") != pack_fingerprint or data.get("resource_version") != version:
        raise DisplayResourceError(f"冻结的显示资源 {version} 与目录对不上")
    return DisplayResources(
        pack_fingerprint=pack_fingerprint,
        resource_version=version,
        templates=data["templates"],
        entities=data["entities"],
        geography=data.get("geography"),
    )


def resources(
    pack_fingerprint: str, version: str, *, frozen: Path | None = None
) -> DisplayResources | None:
    """按资料包与资源版本读冻结资源；没有这个版本返回 ``None``（不回退到当前版本）。"""
    return _load_frozen(str(frozen or FROZEN), pack_fingerprint, version)


# 路径在调用时读模块常量（不写成默认参数），测试可以把登记表与冻结目录整体换到临时目录


def registry(path: Path | None = None) -> dict[str, Any]:
    path = path or REGISTRY
    if not path.is_file():
        return {}
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("packs", {}) or {}


def current_version(pack_fingerprint: str, path: Path | None = None) -> str | None:
    """这个资料包当前登记的显示资源版本；没有登记就是 ``None``（显示附层标为不可用，业务读取照常）。"""
    entry = registry(path).get(pack_fingerprint)
    return None if entry is None else entry.get("current")


def register(pack_fingerprint: str, pack_id: str, version: str, path: Path | None = None) -> None:
    """把一个冻结版本登记为这个资料包当前的版本（旧版本照样保留在登记表与冻结目录里）。"""
    path = path or REGISTRY
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None) or {}
    packs = data.setdefault("packs", {})
    entry = packs.setdefault(pack_fingerprint, {"pack_id": pack_id, "versions": []})
    if version not in entry["versions"]:
        entry["versions"].append(version)
    entry["current"] = version
    entry["pack_id"] = pack_id
    header = (
        "# 显示资源登记表（product.display.catalog）：每个资料包当前用哪个冻结版本；旧版本保留，历史对象按它们记下的版本读取。\n"
        "# 由 scripts/product_display.py freeze 写入，不要手改。\n"
    )
    path.write_text(
        header + yaml.safe_dump(data, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )


def source_resources(
    pack_fingerprint: str, pack_id: str, root: Path | None = None
) -> DisplayResources:
    """不冻结、只在内存里按当前源文件组一份资源（开发与测试核对译文用；服务只读冻结版本）。"""
    content = build_content(pack_fingerprint, pack_id, root or SOURCE)
    return DisplayResources(
        pack_fingerprint=pack_fingerprint,
        resource_version=version_of(content),
        templates=content["templates"],
        entities=content["entities"],
        geography=content["geography"],
    )
