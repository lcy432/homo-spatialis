"""E3B 资料包组装的输入核对与构建身份（独立审核 F2，DECISIONS/0042）。

组装读到的每一份输入都按**实际内容**核对它声明的身份，再用核对过的身份合成构建身份——不信文件里写着的 ``content_identity``：

* 生活机会与职业（``items/``）：每城文件按冻结时的规则重算（去掉 ``identities``，加上芝加哥的活动文件），要等于文件与清单里
  写的；清单自身也重算；
* 通勤（``commute/<城市>.json``）：去掉 ``content_identity`` 重算；
* intake 冻结库：``meta`` 的 ``build_id`` 要等于生活机会清单与通勤资料声明的那一个；组装实际读的行（租住候选、城市事实）另算
  摘要进身份——库里的行变了，身份跟着变；
* 原始地理资料（CBSA 划定、普查区—PUMA 对照）：缓存内容的 SHA-256 要等于 intake 获取阶段记下的；
* 会影响产物的基础配置与模板（产品样例的生活规则、评分规格、评价模板、城市事实模板、产品问卷、职业配置、税与资料包配置）按
  文件内容算摘要；
* 组装规则版本 ``ASSEMBLY_RULES_VERSION``：改了组装规则（代码）就改它。

任何一处对不上就拒绝构建（``InputIdentityError``），不读到另一份内容。菜单种子另算（``menu_settings_identity``，只取菜单设定），
所以改背景值、油费或税不会重抽菜单。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.intake_contracts import FetchRecord, content_identity
from common.life_contracts import canonical_fingerprint
from product.realdata.fetch import Request, fetch

#: 组装规则的版本：改了组装代码里的任何规则（片区、菜单、参考生活的绑定、背景换算、可见文字……）就改它，让构建身份跟着变。
ASSEMBLY_RULES_VERSION = "life-pack-assembly-2026-10-23-f2"


class InputIdentityError(RuntimeError):
    """组装读到的内容与它声明的身份对不上：拒绝构建。"""


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class VerifiedItems:
    manifest: dict[str, Any]
    cities: dict[str, dict[str, Any]]
    identity: str


def items_content(body: dict[str, Any], programs: list[Any]) -> str:
    """一城生活机会文件的内容身份（与 ``life_freeze`` 冻结时同一规则）。"""
    return canonical_fingerprint(
        {k: v for k, v in body.items() if k not in ("content_identity", "identities")}
        | {"programs": programs}
    )


def verify_items(items_dir: Path, cbsas: Iterable[str]) -> VerifiedItems:
    """按内容重算生活机会清单与每城文件的身份；任何一处与声明不符就拒绝。"""
    manifest = _json(items_dir / "manifest.json")
    if canonical_fingerprint(
        {k: v for k, v in manifest.items() if k != "content_identity"}
    ) != manifest.get("content_identity"):
        raise InputIdentityError(f"{items_dir}/manifest.json：清单内容与它声明的身份对不上")
    cities: dict[str, dict[str, Any]] = {}
    actual: dict[str, str] = {}
    for cbsa in cbsas:
        body = _json(items_dir / f"{cbsa}.json")
        pf = items_dir / f"{cbsa}.programs.json"
        programs = _json(pf)["items"] if pf.exists() else []
        got = items_content(body, programs)
        declared = {body.get("content_identity"), manifest["cities"][cbsa]["content_identity"]}
        if declared != {got}:
            raise InputIdentityError(
                f"{items_dir}/{cbsa}.json：按内容重算的身份 {got[:12]}… 与文件或清单声明的不同——内容改了而身份没有跟着重算"
            )
        cities[cbsa] = body
        actual[cbsa] = got
    return VerifiedItems(
        manifest=manifest,
        cities=cities,
        identity=canonical_fingerprint({"manifest": manifest["content_identity"], **actual}),
    )


def verify_commute(commute_dir: Path, keys: Iterable[str]) -> dict[str, tuple[dict[str, Any], str]]:
    """按内容重算每城通勤资料的身份；返回（内容，身份）。"""
    out: dict[str, tuple[dict[str, Any], str]] = {}
    for key in keys:
        payload = _json(commute_dir / f"{key}.json")
        got = content_identity({k: v for k, v in payload.items() if k != "content_identity"})
        if got != payload.get("content_identity"):
            raise InputIdentityError(
                f"{commute_dir}/{key}.json：按内容重算的身份 {got[:12]}… 与声明的不同——内容改了而身份没有跟着重算"
            )
        out[key] = (payload, got)
    return out


def verify_intake_build(con: sqlite3.Connection, declared: set[str]) -> str:
    """intake 冻结库的构建身份必须等于各份资料声明的那一个（全部声明一致，而且等于库里记的）。"""
    meta = dict(con.execute("select key, value from meta"))
    got = meta.get("build_id")
    if declared != {got}:
        raise InputIdentityError(
            f"intake 冻结库的构建身份 {str(got)[:12]}… 与资料声明的 {sorted(d[:12] for d in declared)} 不一致"
        )
    return str(got)


def intake_fetch_log(con: sqlite3.Connection) -> dict[str, FetchRecord]:
    return {
        n: FetchRecord.model_validate_json(j)
        for n, j in con.execute("select name, json from fetches")
    }


def pinned_raw_files(
    requests: dict[str, Request], cache: Path, log: dict[str, FetchRecord]
) -> tuple[dict[str, bytes], dict[str, str]]:
    """按 intake 获取阶段记下的请求与 SHA-256 读原始文件；返回（内容，SHA-256）。缓存里的内容不是记下的那一份就拒绝。"""
    content: dict[str, bytes] = {}
    digests: dict[str, str] = {}
    for name, req in requests.items():
        rec = log.get(name)
        if rec is None or rec.status != "ok" or rec.raw_sha256 is None:
            raise InputIdentityError(f"{name}：intake 获取阶段没有取得这份原始文件")
        if rec.request_key != req.key:
            raise InputIdentityError(f"{name}：请求与 intake 获取阶段记下的不同")
        r = fetch(req, cache, "offline")
        if not r.ok or r.content is None:
            raise InputIdentityError(f"{name}：缓存里读不到这份原始文件（{r.record.status}）")
        sha = hashlib.sha256(r.content).hexdigest()
        if sha != rec.raw_sha256:
            raise InputIdentityError(
                f"{name}：缓存里的内容（SHA-256 {sha[:12]}…）不是 intake 记下的那一份（{rec.raw_sha256[:12]}…）"
            )
        content[name] = r.content
        digests[name] = sha
    return content, digests


def rows_digest(rows: Any) -> str:
    """组装实际读到的库行（规范 JSON）的摘要。"""
    return canonical_fingerprint(rows)


def file_digests(paths: Iterable[Path]) -> dict[str, str]:
    """基础配置与模板：按文件内容（字节）算 SHA-256。"""
    return {Path(p).as_posix(): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}


def menu_settings_identity(cfg: dict[str, Any]) -> str:
    """菜单种子用的身份：只取菜单设定（算法、配置种子、每层个数、年数、环境重复数）。不含任何资料身份、评分条件或税——加权
    无放回抽样的键按“种子 + 编号”逐项算，资料改动只经由抽样池本身（候选与权重）改变菜单。"""
    return canonical_fingerprint(
        {
            "menus": cfg["menus"],
            "simulation_years": cfg["simulation_years"],
            "env_repeats": cfg["env_repeats"],
        }
    )


def build_identity(parts: dict[str, Any]) -> str:
    """构建身份：核对过的全部输入身份与组装规则版本。"""
    return canonical_fingerprint({"assembly_rules": ASSEMBLY_RULES_VERSION, **parts})
