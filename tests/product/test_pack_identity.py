"""资料包的构建身份按实际读到的内容核对（独立审核 F2，DECISIONS/0042）。

审核时的探针：把纽约“自己做·主食为主”的月价 120.24 改成 121.24，文件里写着的内容身份不动，旧的组装照样把清单里写着的身份当
构建身份——内容变了、编号不变。现在组装先按内容重算每份输入的身份，与声明不符就拒绝；合法更新（重算并写回身份）得到新身份；
菜单种子只取菜单设定，改背景值不重抽菜单。全部用临时目录里的副本与手写的小库，不读 intake 冻结库。
"""

from __future__ import annotations

import copy
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from common.intake_contracts import FetchRecord, content_identity
from common.life_contracts import canonical_fingerprint
from product.realdata.fetch import Request, _store
from product.realdata.life_pack import load_pack_config
from product.realdata.pack_inputs import (
    InputIdentityError,
    build_identity,
    file_digests,
    menu_settings_identity,
    pinned_raw_files,
    rows_digest,
    verify_commute,
    verify_intake_build,
    verify_items,
)

FROZEN = Path("data/frozen/life/us-v1")
CBSAS = ["35620", "31080", "41860", "16980", "26420", "42660"]
KEYS = ["nyc", "la", "sf", "chi", "hou", "sea"]


@pytest.fixture
def items_copy(tmp_path):
    out = tmp_path / "items"
    shutil.copytree(FROZEN / "items", out)
    return out


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _write(p: Path, body: dict) -> None:
    p.write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _bump_nyc_staples(items: Path) -> None:
    f = items / "35620.json"
    body = _read(f)
    for it in body["items"]:
        o = it["opportunity"]
        if o["opportunity_id"] == "nyc.food.home_staples":
            assert o["price"]["amount"]["amount"] == 120.24
            o["price"]["amount"]["amount"] = 121.24
    _write(f, body)


def test_the_declared_identity_alone_does_not_notice_a_changed_price(items_copy):
    """审核复现：改价之后，文件与清单里写着的身份都没动（旧做法只读这两处）。"""
    before = _read(items_copy / "manifest.json")["content_identity"]
    _bump_nyc_staples(items_copy)
    assert _read(items_copy / "manifest.json")["content_identity"] == before
    assert (
        _read(items_copy / "35620.json")["content_identity"]
        == (_read(FROZEN / "items" / "35620.json")["content_identity"])
    )


def test_a_changed_price_with_the_old_identity_is_refused(items_copy):
    verify_items(items_copy, CBSAS)  # 原样的副本通过
    _bump_nyc_staples(items_copy)
    with pytest.raises(InputIdentityError, match="35620"):
        verify_items(items_copy, CBSAS)


def test_a_legal_update_gets_a_new_identity(items_copy):
    old = verify_items(items_copy, CBSAS).identity
    _bump_nyc_staples(items_copy)
    # 合法更新：按冻结时同一规则重算城市文件与清单的身份并写回
    f = items_copy / "35620.json"
    body = _read(f)
    content = canonical_fingerprint(
        {k: v for k, v in body.items() if k not in ("content_identity", "identities")}
        | {"programs": []}
    )
    body["content_identity"] = content
    _write(f, body)
    m = _read(items_copy / "manifest.json")
    m["cities"]["35620"]["content_identity"] = content
    m["content_identity"] = canonical_fingerprint(
        {k: v for k, v in m.items() if k != "content_identity"}
    )
    _write(items_copy / "manifest.json", m)
    new = verify_items(items_copy, CBSAS).identity
    assert new != old
    assert verify_items(items_copy, CBSAS).identity == new  # 同样的输入，同样的身份


def test_a_manifest_that_does_not_match_its_own_content_is_refused(items_copy):
    m = _read(items_copy / "manifest.json")
    m["numeraire"] = "USD_2023_LOCAL"
    _write(items_copy / "manifest.json", m)
    with pytest.raises(InputIdentityError, match="清单"):
        verify_items(items_copy, CBSAS)


def test_commute_with_changed_minutes_and_the_old_identity_is_refused(tmp_path):
    out = tmp_path / "commute"
    shutil.copytree(FROZEN / "commute", out)
    assert set(verify_commute(out, KEYS)) == set(KEYS)
    body = _read(out / "sf.json")
    body["commute"]["legs"][0]["roundtrip_minutes"] += 10
    _write(out / "sf.json", body)
    with pytest.raises(InputIdentityError, match="sf"):
        verify_commute(out, KEYS)
    body["content_identity"] = content_identity(
        {k: v for k, v in body.items() if k != "content_identity"}
    )
    _write(out / "sf.json", body)
    assert verify_commute(out, KEYS)["sf"][1] == body["content_identity"]


def _db(path: Path, build_id: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("create table meta (key text primary key, value text not null)")
    con.execute("insert into meta values ('build_id', ?)", (build_id,))
    con.commit()
    return con


def test_the_intake_build_must_be_the_declared_one(tmp_path):
    con = _db(tmp_path / "intake.sqlite", "a" * 64)
    assert verify_intake_build(con, {"a" * 64}) == "a" * 64
    with pytest.raises(InputIdentityError, match="intake"):
        verify_intake_build(con, {"a" * 64, "b" * 64})


def test_read_rows_enter_the_identity():
    rows = [{"opportunity_id": "x.housing.1", "price": 1000}]
    changed = copy.deepcopy(rows)
    changed[0]["price"] = 1001
    assert rows_digest(rows) != rows_digest(changed)
    assert rows_digest(rows) == rows_digest(copy.deepcopy(rows))


def test_raw_geography_must_be_the_one_the_intake_fetched(tmp_path):
    req = Request(source_id="geo_test", url="https://example.invalid/geo.txt", expect="any")
    _store(tmp_path, req, b"county list v1", 200, "text/plain")
    import hashlib

    good = hashlib.sha256(b"county list v1").hexdigest()

    def log(sha: str) -> dict[str, FetchRecord]:
        return {
            "geo_test": FetchRecord(
                source_id="geo_test",
                url=req.url,
                request_key=req.key,
                status="ok",
                raw_sha256=sha,
                bytes=14,
            )
        }

    content, digests = pinned_raw_files({"geo_test": req}, tmp_path, log(good))
    assert content["geo_test"] == b"county list v1" and digests == {"geo_test": good}
    with pytest.raises(InputIdentityError, match="geo_test"):
        pinned_raw_files({"geo_test": req}, tmp_path, log("0" * 64))


def test_base_files_enter_the_identity(tmp_path):
    a = tmp_path / "evaluation_product.yaml"
    a.write_text("templates: [p1]\n", encoding="utf-8")
    first = file_digests([a])
    a.write_text("templates: [p1, p2]\n", encoding="utf-8")
    assert file_digests([a]) != first


def test_backgrounds_change_the_build_but_not_the_menus():
    cfg = load_pack_config()
    leisure = copy.deepcopy(cfg)
    leisure["conditions"]["leisure"]["base"] = 4
    assert menu_settings_identity(leisure) == menu_settings_identity(cfg)
    parts = {"config": canonical_fingerprint(cfg), "items": "i"}
    other = {"config": canonical_fingerprint(leisure), "items": "i"}
    assert build_identity(parts) != build_identity(other)
    seed = copy.deepcopy(cfg)
    seed["menus"]["seed"] = "another-seed"
    assert menu_settings_identity(seed) != menu_settings_identity(cfg)
