"""仓库里登记与冻结的显示资源（``configs/product/display/``、``data/display/``、``contracts/product/display/v1/``）。

* 代码里的中文模板与资源里的中文副本逐字相同（不同就是译文过时），一个 key 只有一种中文；
* 每个资料包当前登记的版本已经冻结、没被改过，而且就是当前源文件冻结出来的那一版（改了源文件就要重新冻结、登记新版本）；
  登记表里的旧版本都还在，冻结目录里没有不在登记表里的版本；
* 随 E5 携带的固定双语错误目录与当前资源一致，每个错误码与到期对象都有英文（未登录时也能显示）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from product.display import catalog
from product.pack import load_pack

REPO = Path(__file__).resolve().parents[2]
PACKS = ("configs/product/e3a_synthetic_pack.yaml", "configs/product/e3b_us_v1_pack.yaml")


def _cli():
    spec = importlib.util.spec_from_file_location(
        "product_display_cli", REPO / "scripts/product_display.py"
    )
    assert spec is not None and spec.loader is not None
    sys.path.insert(0, str(REPO / "scripts"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_code_and_resources_agree_on_every_chinese_template():
    bad, _missing = _cli().problems()
    assert bad == []


def test_registered_versions_are_frozen_untampered_and_current():
    reg = catalog.registry()
    for rel in PACKS:
        pack = load_pack(REPO / rel)
        entry = reg[pack.fingerprint]
        assert entry["pack_id"] == pack.body.pack_id
        content = catalog.build_content(pack.fingerprint, pack.body.pack_id)
        assert entry["current"] == catalog.version_of(content), f"{rel}：源文件改过，要重新冻结"
        for version in entry["versions"]:
            res = catalog.resources(pack.fingerprint, version)  # 摘要不对会直接报错
            assert res is not None and res.resource_version == version
        frozen = {p.name for p in (catalog.FROZEN / pack.fingerprint).iterdir() if p.is_dir()}
        assert frozen == set(entry["versions"])
    assert {p.name for p in catalog.FROZEN.iterdir() if p.is_dir()} == set(reg)


def test_the_bundled_error_catalog_is_current_and_fully_bilingual():
    cli = _cli()
    committed = (REPO / "contracts/product/display/v1/service-errors.json").read_text(
        encoding="utf-8"
    )
    assert committed == cli.error_catalog_text()
    body = json.loads(committed)
    for group in ("errors", "expired_subjects"):
        for code, entry in body[group].items():
            for part in ("message", "impact", "next"):
                assert entry["zh"][part] and entry["en"][part], (group, code, part)
