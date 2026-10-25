"""E3 → E5 收尾修复（独立复核 F1–F3 与 E5 提出的四项小接口问题、色阶说明）。

* F1：显示资源坏了、缺了、登记表读不出来，都只让显示附层缺失；原业务回复、状态码与中文原文不变，确认只保存一次；离线核对照样报错。
* F2：同名实体各保留自己的身份（按生成的对象与位置绑定，不按中文）。
* F3：历史正文用它自己保存的绑定与资源版本；外层 ``resource_version`` 对历史对象指向它绑定的版本，运行意图还没有绑定时为空。
* 知悉项说明与作答状态：结构化、与原编号 / 正式状态一一对应；审阅说明是人能读的文字；启动前检查数据目录路径长度。

全部离线。显示资源的登记表与冻结目录换到每个测试自己的临时目录（``publish``），损坏的只是测试自己的冻结副本。
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from common.product_contracts import (
    DisplayEnvelope,
    LeisureAnswerState,
    TasteAnswerState,
)
from product.display import catalog
from product.display.catalog import DisplayResourceError
from product.service.config import (
    RUN_PATH_RELATIVE,
    DataDirTooLongError,
    check_data_dir,
)
from tests.product_service.support import (
    API,
    E3B,
    ORIGIN,
    Browser,
    db_dump,
    files_under,
    finished_run,
    ready_intent,
    start,
)
from tests.product_service.test_display_service import _copy_source_with_marked_english, display


@pytest.fixture
def publish(tmp_path, monkeypatch):
    """登记表与冻结目录换到这个测试的临时目录（与 ``test_display_service`` 同样的夹具）。"""
    monkeypatch.setattr(catalog, "FROZEN", tmp_path / "display-frozen")
    monkeypatch.setattr(catalog, "REGISTRY", tmp_path / "display-registry.yaml")

    def run(pack, root=None):
        version = catalog.freeze(pack.fingerprint, pack.body.pack_id, root=root)
        catalog.register(pack.fingerprint, pack.body.pack_id, version)
        return version

    yield run
    catalog._load_frozen.cache_clear()


def _corrupt(svc, version):
    """改坏测试自己的那份冻结资源（不是仓库里的），并清掉只读缓存，模拟重启后第一次读。"""
    target = catalog.FROZEN / svc.ctx.pack.fingerprint / version / "resources.json"
    target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")
    catalog._load_frozen.cache_clear()


def _confirm_body(review):
    return {
        "review_id": review["review"]["review_id"],
        "draft_id": review["review"]["draft"]["draft_id"],
        "draft_revision": review["reviewed_revision"],
        "acknowledged": review["review"]["pending_confirmations"],
    }


# ---------------------------------------------------------------------- F1：显示故障不破坏业务回复


def test_corrupt_resources_leave_errors_as_safe_json_and_english_missing(tmp_path, publish):
    svc = start(tmp_path)
    version = publish(svc.ctx.pack)
    _corrupt(svc, version)
    with pytest.raises(DisplayResourceError):  # 离线核对照样严格：坏资源一段也不用
        catalog.resources(svc.ctx.pack.fingerprint, version)
    catalog._load_frozen.cache_clear()
    anonymous = TestClient(svc.app, base_url=ORIGIN, raise_server_exceptions=False)
    r = anonymous.get(API + "/session")
    assert r.status_code == 401 and r.json()["code"] == "unauthenticated"
    att = r.json()["display"]
    assert att["translations"]["zh"]["status"] == "complete"
    en = att["translations"]["en"]
    assert en["status"] == "unavailable"
    assert {f["missing_reason"] for f in en["fields"]} == {"resources_invalid"}
    # 显示出口照样 200：原消息完整，英文照实缺失
    _, b = svc.person()
    env = display(b, "capabilities", None, "en").json()
    assert env["status"] == "unavailable" and env["source"] == b.get("/capabilities").json()
    assert {f["missing_reason"] for f in env["fields"]} <= {"resources_invalid", "no_binding"}
    geo = b.get("/display/geography", params={"pack_fingerprint": svc.ctx.pack.fingerprint})
    assert geo.status_code == 200 and geo.json()["status"] == "unavailable" and geo.json()["notes"]


def test_a_saved_confirmation_is_reported_as_saved_once_even_if_display_fails(tmp_path, publish):
    svc = start(tmp_path)
    version = publish(svc.ctx.pack)
    principal, b = svc.person()
    did = b.fill(b.new_draft())["draft"]["draft_id"]
    review = b.post(f"/drafts/{did}/review").json()
    _corrupt(svc, version)
    events = svc.store.quota_events(principal)
    client = TestClient(svc.app, base_url=ORIGIN, raise_server_exceptions=False)
    client.cookies.update(b.client.cookies)
    first = client.post(API + "/profiles/confirm", headers=b.headers(), json=_confirm_body(review))
    assert first.status_code == 200 and first.json()["status"] == "confirmed"
    again = client.post(API + "/profiles/confirm", headers=b.headers(), json=_confirm_body(review))
    assert again.json()["confirmed"]["confirmed_id"] == first.json()["confirmed"]["confirmed_id"]
    assert len(b.get("/profiles").json()["profiles"]) == 1  # 只保存一次
    assert svc.store.quota_events(principal) == events  # 没有扣次、没有排队
    cid = first.json()["confirmed"]["confirmed_id"]
    assert b.get(f"/profiles/{cid}").status_code == 200


def test_a_broken_registry_or_missing_version_only_hides_the_display(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, b = svc.person()
    catalog.REGISTRY.write_text("packs: [this is: not, a mapping", encoding="utf-8")
    r = b.get("/capabilities")
    assert r.status_code == 200
    env = display(b, "capabilities", None, "en").json()
    assert {f["missing_reason"] for f in env["fields"]} <= {"resources_invalid", "no_binding"}
    assert TestClient(svc.app, base_url=ORIGIN).get(API + "/session").status_code == 401
    # 登记表指向一个不存在的版本：照实缺失，不回退
    catalog.REGISTRY.write_text(
        yaml.safe_dump(
            {
                "packs": {
                    svc.ctx.pack.fingerprint: {
                        "current": "e3-display-res-v1-0000000000000000",
                        "pack_id": svc.ctx.pack.body.pack_id,
                        "versions": ["e3-display-res-v1-0000000000000000"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    env = display(b, "capabilities", None, "en").json()
    assert {f["missing_reason"] for f in env["fields"]} <= {"resources_unavailable", "no_binding"}
    assert (
        b.get(
            "/display/geography", params={"pack_fingerprint": svc.ctx.pack.fingerprint}
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------- F2：同名实体不串绑定（服务里的真实消息）


def test_cities_with_the_same_item_names_keep_their_own_entities(tmp_path, publish):
    """合成资料包两座城的岗位、机会与假设名称一样：每座城的位置都绑定到它自己那座城的编号。"""
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, b = svc.person()
    view = ready_intent(svc, b)
    env = display(b, "intent", view["intent_id"], "en").json()
    cities = env["source"]["preflight"]["cities"]
    checked = 0
    for i, city in enumerate(cities):
        prefix = city["city_ref"].rsplit(":", 1)[1].lower() + "."
        for f in env["fields"]:
            if f["pointer"].startswith(f"/preflight/cities/{i}/") and f["key"].startswith(
                ("entity.opportunity.", "entity.assumption.")
            ):
                assert f["key"].split(":", 1)[1].startswith(prefix), f
                checked += 1
    assert checked >= 4


def test_a_saved_draft_shows_its_pack_note_only_when_it_is_the_same_pack(tmp_path, publish):
    """草稿是读回的保存内容：它自带的资料包说明与外层当前的资料包块完全相同，才按位置对带过外层的绑定。"""
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, b = svc.person()
    did = b.fill(b.new_draft())["draft"]["draft_id"]
    env = display(b, "draft", did, "en").json()
    inner = next(f for f in env["fields"] if f["pointer"] == "/draft/meta/data_pack/note")
    outer = next(f for f in env["fields"] if f["pointer"] == "/meta/data_pack/note")
    assert inner["status"] == "provided" and inner["text"] == outer["text"]


# ---------------------------------------------------------------------- F3：历史正文与当前服务说明分开


def test_a_new_release_does_not_reinterpret_history(tmp_path, publish):
    svc = start(tmp_path)
    v1 = publish(svc.ctx.pack)
    _, b = svc.person()
    done = finished_run(svc, b)
    cid = b.get("/profiles").json()["profiles"][0]["confirmed_id"]
    before = {
        kind: display(b, kind, ident, "en").json()
        for kind, ident in (
            ("profile", cid),
            ("intent", done["intent_id"]),
            ("result", done["run_id"]),
            ("progress", done["run_id"]),
        )
    }
    _copy_source_with_marked_english(tmp_path / "src-v2")
    v2 = publish(svc.ctx.pack, root=tmp_path / "src-v2")
    db = db_dump(svc.store.path)
    for kind, old in before.items():
        now = display(b, kind, old["id"], "en").json()
        assert now["source"] == old["source"]
        assert now["resource_version"] == v1, kind  # 历史对象指向它绑定的版本
        historical = {
            "profile": "/confirmed/",
            "intent": "/preflight/",
            "result": "/",
            "progress": "/",
        }[kind]
        old_text = {f["pointer"]: f["text"] for f in old["fields"]}
        for f in now["fields"]:
            if f["pointer"].startswith(historical) and f["status"] == "provided":
                assert f["text"] == old_text[f["pointer"]], (kind, f["pointer"])
                assert f["resource_version"] == v1
    assert db_dump(svc.store.path) == db  # 读取不写库、不续期、不补写
    live = display(b, "capabilities", None, "en").json()
    assert live["resource_version"] == v2  # 当场生成的对象用当前版本


def test_an_intent_without_a_bound_pre_run_check_has_no_version(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, b = svc.person()
    intent = b.prepare()  # 检查还在排队，还没有绑定
    env = display(b, "intent", intent["intent_id"], "en").json()
    assert env["source"]["preflight"] is None and env["resource_version"] is None


# ---------------------------------------------------------------------- 知悉项说明


def test_acknowledgements_match_the_pending_ids_and_do_not_replace_them(tmp_path, publish):
    from product.answers import ack_display

    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, b = svc.person()
    # 新问卷不再收集属性偏好（没有“只留存的陈述”）：待确认的是标准开局与“本次不按兴趣或口味个性化”
    filled = b.fill(b.new_draft())
    did = filled["draft"]["draft_id"]
    review = b.post(f"/drafts/{did}/review").json()
    pending = review["review"]["pending_confirmations"]
    assert pending == ["ack:interests_not_personalized", "ack:standard_start"]
    for locale in ("zh", "en"):
        env = display(b, "review", review["review"]["review_id"], locale).json()
        acks = env["acknowledgements"]
        assert [a["ack_id"] for a in acks] == pending
        for a in acks:
            assert a["text"]["zh"] == ack_display(a["ack_id"])
            assert a["statement_pointer"] is None  # 没有留存的陈述
            assert a["text"]["status"] == "provided" and a["text"]["en"]
    # 确认照旧提交原来的编号；显示条目不勾选、不替用户确认
    r = b.post("/profiles/confirm", json=_confirm_body(review))
    assert r.json()["status"] == "confirmed"


def test_an_old_review_without_saved_acknowledgements_reports_them_missing(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, b = svc.person()
    did = b.fill(b.new_draft())["draft"]["draft_id"]
    review = b.post(f"/drafts/{did}/review").json()
    rid = review["review"]["review_id"]
    con = sqlite3.connect(svc.store.path)
    try:
        doc = json.loads(
            con.execute("SELECT display_body FROM reviews WHERE review_id = ?", (rid,)).fetchone()[
                0
            ]
        )
        doc.pop("acknowledgements")
        con.execute(
            "UPDATE reviews SET display_body = ? WHERE review_id = ?", (json.dumps(doc), rid)
        )
        con.commit()
    finally:
        con.close()
    acks = display(b, "review", rid, "en").json()["acknowledgements"]
    assert [a["ack_id"] for a in acks] == review["review"]["pending_confirmations"]
    assert all(
        a["text"]["zh"] is None and a["text"]["missing_reason"] == "no_binding" for a in acks
    )


# ---------------------------------------------------------------------- 作答状态


def test_answer_states_are_given_by_state_not_by_list_position(tmp_path, publish, monkeypatch):
    from product import interests

    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, b = svc.person()
    env = display(b, "capabilities", None, "en").json()
    states = {(s["answer"], s["state"]): s for s in env["answer_states"]}
    expected = [("leisure", s) for s in LeisureAnswerState.__args__] + [
        ("taste", s) for s in TasteAnswerState.__args__
    ]
    assert list(states) == expected
    assert all(s["name"]["status"] == "provided" and s["meaning"]["en"] for s in states.values())
    generic = states["leisure", "generic"]
    assert generic["name"]["zh"] == "采用通用休闲评价"
    # 把内部的状态表倒过来排：每个状态仍是它自己的名称与含义（按状态，不按顺序）
    flipped = {
        k: dict(reversed(list(v.items())))
        for k, v in reversed(list(interests.ANSWER_STATES.items()))
    }
    monkeypatch.setattr(interests, "ANSWER_STATES", flipped)
    again = {
        (s["answer"], s["state"]): s
        for s in display(b, "capabilities", None, "en").json()["answer_states"]
    }
    assert again[("leisure", "generic")]["name"] == generic["name"]
    assert again[("taste", "no_preference")]["name"]["zh"] == "明确没有口味偏好"


@pytest.mark.parametrize("pack_path", ["configs/product/e3a_synthetic_pack.yaml", E3B])
def test_answer_state_texts_agree_with_the_pack(pack_path):
    """状态表的说法与资料包的作答说明一致（这里按资料包列表的顺序核对，生成时不靠这个顺序）。"""
    from product.interests import ANSWER_STATES
    from product.pack import load_pack
    from product.service.config import REPO

    pack = load_pack(REPO / pack_path)
    for answer, listed in (
        ("leisure", pack.body.interests.leisure_states_zh),
        ("taste", pack.body.interests.taste_states_zh),
    ):
        table = ANSWER_STATES[answer]
        # 资料包的说明写于面板选择之前：前面几种逐条一致；v3.4 新增的“从面板选择”由 E3 的状态表定义（资料包不改，内容身份不变）
        states = [s for s in table if s != "selected"]
        assert [f"{table[s][0].zh}：{table[s][1].zh}" for s in states] == list(listed)
        assert list(table)[-1] == "selected"


# ---------------------------------------------------------------------- 审阅说明是人能读的文字


def test_review_shows_names_not_program_lists():
    from product.answers import review_draft
    from product.fixtures import make_draft
    from product.pack import load_pack
    from product.service.config import REPO

    pack = load_pack(REPO / E3B)
    draft = make_draft(
        pack, abilities=("edu_at_least_hs",), taste={"state": "described", "text": "喜欢川菜"}
    )
    review = review_draft(pack, draft)
    taste = review.taste
    assert taste.message_zh == "口味与菜系：待选中——候选：中餐；有歧义：无；没认出：无"
    assert taste.candidates == ("chinese",) and taste.text == "喜欢川菜"  # 编号与原文不改
    texts = [taste.message_zh, *(i.message_zh for i in review.issues)]
    assert not [x for x in texts if "['" in x or "[]" in x]


# ---------------------------------------------------------------------- 数据目录路径长度


def test_data_dir_length_check(tmp_path):
    limit = 259 - 1 - RUN_PATH_RELATIVE
    ok = Path("C:/") / ("a" * (limit - 3))
    check_data_dir(ok, windows=True, long_paths=False)
    deep = Path("C:/") / ("a" * (limit + 10))
    with pytest.raises(DataDirTooLongError) as exc:
        check_data_dir(deep, windows=True, long_paths=False)
    assert "数据目录路径过长，请改用较短目录" in str(exc.value)
    assert "a" * 20 not in str(exc.value)  # 不写本机的完整路径
    check_data_dir(deep, windows=True, long_paths=True)  # 开了长路径支持：不误拒
    check_data_dir(deep, windows=False)  # 不是 Windows：不限制


def test_a_too_deep_data_dir_stops_before_creating_anything(tmp_path, monkeypatch):
    from product.service import config as service_config

    deep = tmp_path / "x" / "data"
    real = service_config.check_data_dir

    def strict(root, **_kw):
        real(Path("C:/") / ("a" * 300), windows=True, long_paths=False)

    monkeypatch.setattr("product.service.actions.check_data_dir", strict)
    with pytest.raises(DataDirTooLongError):
        start(deep.parent.parent, data_dir=str(deep))
    assert not deep.exists()  # 没有建服务库、密钥或任何目录


def test_other_file_errors_are_not_called_path_too_long(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(Exception) as exc:
        start(tmp_path, data_dir=str(blocker / "data"))
    assert not isinstance(exc.value, DataDirTooLongError)


def test_a_real_run_never_writes_a_path_longer_than_the_checked_length(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    finished_run(svc, b)
    data = tmp_path / "data"
    longest = max(len(p) for p in files_under(data))
    assert longest + len(".tmp") <= RUN_PATH_RELATIVE
    assert os.path.isdir(data)


# ---------------------------------------------------------------------- 色阶说明


def test_the_colour_notes_allow_a_zoomed_legend_within_one_result(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, b = svc.person()
    done = finished_run(svc, b)
    env = DisplayEnvelope.model_validate(display(b, "result", done["run_id"], "en").json())
    (spec,) = env.metrics
    assert (spec.minimum, spec.maximum) == ("0", "1")
    assert (
        spec.denominator_field == "mean_denominator"
        and spec.comparison_scope == "within_result_only"
    )
    zh = " ".join(n.zh for n in spec.notes)
    assert "ADR 0045" in zh and "窗口两端" in zh and "不暗示最低" in zh
    assert "不按国家或城市重新拉伸" not in zh and "固定尺度" not in zh
    assert Browser  # 保持与其他服务测试相同的导入
