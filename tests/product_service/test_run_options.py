"""运行规模与预算参数化（E3C 收尾第二步）：默认值、允许范围、本次有效设置，以及它们在整条运行链上的效果。

* 默认值不变（不选就是服务默认）；城市子集、不同分身数与预算真的改变这次的有效设置与派生上限，派生上限按规模 × 修订 × 重试算；
* 维护者调高上限之后可以接受更大的规模（代码里没有暗藏“最多 4 个”）；年数用配套的受控夹具资料包验证其他年数；
* 两个用户的设置互不影响；改服务默认值不改变已经创建的运行；
* 越界、重复城市、非法金额、不支持的年数在预留与任何调用之前就拒绝；
* 每次运行一本新账本，账本额度就是这次选的预算。
"""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from orchestration.life_budget import read_ledger
from product.execution import RunOptionsError
from product.service.actions import ServiceContext
from product.service.clock import FakeClock
from product.workspace import WORKSPACE_FILES
from tests.life.live_support import ScriptedTransport
from tests.product_service.support import Service, config, finished_run, ready_intent, start
from workflows.service_worker import Worker

REPO = Path(__file__).resolve().parents[2]
K51, M52 = "US:SYNTH2026:K51", "US:SYNTH2026:M52"


def _settings(view: dict) -> dict:
    return view["settings"]


def test_defaults_are_used_when_nothing_is_chosen(tmp_path):
    svc = start(tmp_path, default_clones_per_city=4, planner="llm", live_calls_enabled=False)
    _, b = svc.person()
    view = ready_intent(svc, b)
    s = _settings(view)
    # 合成资料包两座城 × 默认 4 个分身 × 3 年；每年 1 次规划 + 最多 2 次修订；每份候选最多再试 1 次服务请求
    assert (s["cities"], s["clones_per_city"], s["years"]) == ([K51, M52], 4, 3)
    assert s["clones_total"] == 8 and s["call_limit"] == 2 * 4 * 3 * 3
    assert s["service_attempt_limit"] == 2 * 4 * 3 * 3 * 2
    assert (s["max_cost"], s["currency"]) == ("20", "CNY")
    assert view["preflight"]["clones_per_city"] == 4
    assert view["preflight"]["planner"]["call_limit"] == 72


def test_city_subset_clones_and_budget_change_this_runs_settings(tmp_path):
    svc = start(tmp_path, planner="llm", live_calls_enabled=False)
    _, b = svc.person()
    view = ready_intent(svc, b, {"clones_per_city": 3, "max_cost_cny": "5.5"}, cities=(M52,))
    s = _settings(view)
    assert (s["cities"], s["clones_per_city"], s["years"], s["max_cost"]) == ([M52], 3, 3, "5.5")
    assert s["call_limit"] == 1 * 3 * 3 * 3 and s["service_attempt_limit"] == 1 * 3 * 3 * 3 * 2
    pre = view["preflight"]
    assert [c["city_ref"] for c in pre["cities"]] == [M52] and pre["clones_per_city"] == 3
    assert "5.5 CNY" in pre["planner"]["max_cost"]


def test_a_smaller_run_actually_runs_at_that_scale(tmp_path):
    svc = start(tmp_path, default_clones_per_city=1)
    _, b = svc.person()
    done = finished_run(svc, b, {"clones_per_city": 2}, cities=(K51,))
    result = b.get(f"/runs/{done['run_id']}/result").json()
    assert sorted(c["clone_ref"].rsplit(".", 1)[1] for c in result["clones"]) == ["r0", "r1"]
    assert {c["city_ref"] for c in result["clones"]} == {K51}
    assert all(len(c["years"]) == 3 for c in result["clones"])
    assert result["planner"]["call_limit"] == 1 * 2 * 3 * 3


def test_raising_the_maximum_accepts_a_bigger_scale(tmp_path):
    svc = start(tmp_path, clones_per_city_max=6, default_clones_per_city=1)
    _, b = svc.person()
    done = finished_run(svc, b, {"clones_per_city": 5}, cities=(K51,))
    result = b.get(f"/runs/{done['run_id']}/result").json()
    assert len(result["clones"]) == 5
    # 增加分身不重抽菜单：编号 0…4 由服务生成，菜单分配沿用编译器的规则
    assert sorted(c["clone_ref"].rsplit(".", 1)[1] for c in result["clones"]) == [
        f"r{k}" for k in range(5)
    ]


@pytest.mark.parametrize(
    ("options", "field"),
    [
        ({"clones_per_city": 5}, "clones_per_city"),
        ({"clones_per_city": 0}, "clones_per_city"),
        ({"years": 2}, "years"),
        ({"years": 4}, "years"),
        ({"max_cost_cny": "20.01"}, "max_cost_cny"),
        ({"max_cost_cny": "0"}, "max_cost_cny"),
        ({"max_cost_cny": "0.001"}, "max_cost_cny"),
        ({"max_cost_cny": "abc"}, "max_cost_cny"),
        ({"max_cost_cny": "-1"}, "max_cost_cny"),
        ({"max_cost_cny": "1e3"}, "max_cost_cny"),
        ({"max_cost_cny": 5}, "max_cost_cny"),
    ],
)
def test_out_of_range_options_are_refused_before_any_reservation(tmp_path, options, field):
    svc = start(tmp_path, planner="llm", live_calls_enabled=False)
    principal, b = svc.person()
    confirmed = b.confirm(b.fill(b.new_draft()))
    r = b.post("/run-intents", json={"confirmed_id": confirmed["confirmed_id"], **options})
    assert r.status_code == 409 and r.json()["code"] == "run_options_invalid"
    assert r.json()["field"] == field
    shown = str(options[field])
    if len(shown) >= 3:  # 不回显用户填的值（一两个数字会和状态码等撞上，只查长一些的）
        # 内容身份与资源版本名是十六进制摘要，可能碰巧含有“abc”这样的字母：先去掉它们再查
        assert shown not in re.sub(r"[0-9a-f]{16,}", "", r.text)
    assert set(r.json()) == {
        "meta",
        "status",
        "code",
        "message_zh",
        "impact_zh",
        "next_action_zh",
        "support_ref",
        "retry_after_seconds",
        "field",
        "display",
        "sample",
    }
    assert svc.store.intents(principal) == [] and svc.store.pending_jobs() == []
    assert [e["kind"] for e in svc.store.quota_events(principal)] == []


def test_duplicate_and_unknown_cities_are_refused_by_the_policy(tmp_path):
    svc = start(tmp_path)
    with pytest.raises(RunOptionsError) as dup:
        svc.ctx.policy.resolve((K51, K51))
    assert dup.value.field == "cities"
    with pytest.raises(RunOptionsError) as unknown:
        svc.ctx.policy.resolve(("US:SYNTH2026:X99",))
    assert unknown.value.field == "cities"
    with pytest.raises(RunOptionsError):
        svc.ctx.policy.resolve(())


def test_two_people_keep_their_own_settings(tmp_path):
    svc = start(tmp_path, default_clones_per_city=1)
    _, alice = svc.person("甲")
    _, bob = svc.person("乙")
    a = alice.prepare({"clones_per_city": 2}, cities=(K51,))
    c = bob.prepare({"clones_per_city": 3})
    svc.work()
    a, c = alice.intent(a["intent_id"]), bob.intent(c["intent_id"])
    assert (_settings(a)["cities"], _settings(a)["clones_per_city"]) == ([K51], 2)
    assert (_settings(c)["cities"], _settings(c)["clones_per_city"]) == ([K51, M52], 3)
    assert a["preflight"]["clones_per_city"] == 2 and c["preflight"]["clones_per_city"] == 3


def test_changing_the_service_defaults_does_not_touch_existing_runs(tmp_path):
    clock = FakeClock()
    svc = start(tmp_path, clock=clock, default_clones_per_city=1)
    principal, b = svc.person()
    queued = ready_intent(svc, b)
    assert b.submit(queued).status_code == 202
    before = svc.store.intent(principal, queued["intent_id"])["execution_body"]
    # 维护者把默认改成每城 3 个分身、预算 7 元，重启服务：已经排队的运行照旧
    cfg = config(tmp_path / "data", default_clones_per_city=3, max_cost_cny="7")
    ctx = ServiceContext.open(cfg, clock=clock)
    svc2 = Service(ctx=ctx, clock=clock, app=svc.app, worker=Worker(ctx, sleep=lambda _s: None))
    assert svc2.store.intent(principal, queued["intent_id"])["execution_body"] == before
    svc2.work()
    done = b.intent(queued["intent_id"])
    assert done["stage"] == "finished" and _settings(done)["clones_per_city"] == 1
    result = b.get(f"/runs/{done['run_id']}/result").json()
    assert len(result["clones"]) == 2  # 两座城 × 原来的 1 个分身


def test_a_changed_policy_blocks_an_unconfirmed_intent_instead_of_replacing_it(tmp_path):
    svc = start(tmp_path, clones_per_city_max=4)
    _, b = svc.person()
    view = ready_intent(svc, b, {"clones_per_city": 4})
    svc.ctx.policy = dataclasses.replace(svc.ctx.policy, clones_max=2)  # 维护者收窄了允许范围
    r = b.submit(view)
    assert r.status_code == 409 and r.json()["code"] == "run_options_invalid"
    assert r.json()["field"] == "clones_per_city"
    after = b.intent(view["intent_id"])
    assert after["stage"] == "blocked" and "cancel" in after["allowed_actions"]
    assert after["explanation"]["category"] == "service_options_not_allowed"
    assert _settings(after)["clones_per_city"] == 4  # 没有被静默改成别的规模
    assert b.quota()["reserved"] == 0


def test_the_ledger_is_new_per_run_and_carries_this_runs_budget(tmp_path):
    transport = ScriptedTransport()
    svc = start(
        tmp_path,
        planner="llm",
        live_calls_enabled=True,
        transport=lambda _s: transport,
        workers=2,
    )
    _, b = svc.person()
    first = finished_run(svc, b, {"max_cost_cny": "3"})
    second = finished_run(svc, b, {"max_cost_cny": "4.25"})
    ledgers = {
        v["intent_id"]: read_ledger(svc.ctx.run_dir(v["intent_id"]) / "ws" / "budget")
        for v in (first, second)
    }
    assert Decimal(ledgers[first["intent_id"]][0]["max_cost_cny"]) == Decimal("3")
    assert Decimal(ledgers[second["intent_id"]][0]["max_cost_cny"]) == Decimal("4.25")
    # 两本账互相独立：第二次运行的预留里没有第一次的请求
    keys = [{e["key"] for e in ledger[1] if e["event"] == "reserve"} for ledger in ledgers.values()]
    assert keys[0] and keys[1] and not keys[0] & keys[1]


# ---------------------------------------------------------------------- 年数：配套的受控夹具


def _fixture_repo(tmp: Path, years: list[int]) -> Path:
    """一份受控夹具“仓库”：工作目录需要的文件照原样复制；合成资料包在运行声明里多写 ``years_supported``（资料包配套声明支持的
    年数）。只在临时目录里，不改仓库的资料。"""
    for rel in (*WORKSPACE_FILES, "configs/life/run_product_flash.yaml", "configs/models.yaml"):
        dst = tmp / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dst)
    pack = tmp / "configs/product/e3a_synthetic_pack.yaml"
    raw = yaml.safe_load(pack.read_text(encoding="utf-8"))
    raw["pack"]["run"]["years_supported"] = years
    pack.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return tmp


def test_other_year_counts_need_a_pack_that_declares_them(tmp_path):
    repo = _fixture_repo(tmp_path / "repo", [2, 3])
    clock = FakeClock()
    ctx = ServiceContext.open(
        config(tmp_path / "data", default_clones_per_city=1), clock=clock, repo=repo
    )
    svc = Service(ctx=ctx, clock=clock, app=None, worker=Worker(ctx, sleep=lambda _s: None))
    from product.service.api import create_app

    svc.app = create_app(ctx)
    _, b = svc.person()
    rules = b.get("/session").json()["rules"]
    assert rules["options"]["years_allowed"] == [2, 3] and rules["options"]["years_default"] == 3
    done = finished_run(svc, b, {"years": 2}, cities=(K51,))
    assert _settings(done)["years"] == 2 and _settings(done)["call_limit"] == 1 * 1 * 2 * 3
    result = b.get(f"/runs/{done['run_id']}/result").json()
    assert all(len(c["years"]) == 2 for c in result["clones"])
    # 没有声明的年数照样拒绝
    confirmed = b.confirm(b.fill(b.new_draft()))
    r = b.post("/run-intents", json={"confirmed_id": confirmed["confirmed_id"], "years": 4})
    assert r.status_code == 409 and r.json()["field"] == "years"


def test_the_e3b_pack_opens_only_its_validated_three_years(tmp_path):
    svc = start(
        tmp_path, pack_path="configs/product/e3b_us_v1_pack.yaml", default_clones_per_city=4
    )
    rules = svc.ctx.policy
    assert rules.years_allowed == (3,) and rules.years_default == 3
    assert len(rules.cities_allowed) == 6
    spec = svc.ctx.default_spec
    assert (len(spec.cities), len(spec.repeat_indexes), spec.years) == (6, 4, 3)
    assert spec.global_call_limit == 6 * 4 * 3 * 3


def test_settings_are_saved_when_the_intent_is_created(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    view = b.prepare({"clones_per_city": 2})
    row = svc.store.intent(principal, view["intent_id"])
    saved = json.loads(row["execution_body"])
    assert (
        saved["repeat_indexes"] == [0, 1] and json.loads(row["run_options"])["clones_per_city"] == 2
    )
    # 没有排队检查之前就已经保存：worker 用的是这一份，不读服务现在的默认值
    assert row["stage"] == "checking"


def test_the_same_settings_return_the_original_intent_and_other_settings_conflict(tmp_path):
    svc = start(tmp_path, default_clones_per_city=1)
    principal, b = svc.person()
    first = b.prepare({"clones_per_city": 2, "max_cost_cny": "5"})
    cid = first["confirmed"]["confirmed_id"]
    # 同一份确认画像、同样的有效设置（金额写法不同、值相同）：返回原意图
    again = b.post(
        "/run-intents", json={"confirmed_id": cid, "clones_per_city": 2, "max_cost_cny": "5.00"}
    )
    assert again.status_code == 200 and again.json()["intent_id"] == first["intent_id"]
    # 设置不同（省略 = 服务默认值，也算不同）：冲突，不替换原意图
    for other in (
        {"clones_per_city": 3, "max_cost_cny": "5"},
        {"clones_per_city": 2, "max_cost_cny": "6"},
        {"clones_per_city": 2},
        {},
    ):
        r = b.post("/run-intents", json={"confirmed_id": cid, **other})
        assert r.status_code == 409 and r.json()["code"] == "intent_conflict"
    assert b.get("/session").json()["open_intent_id"] == first["intent_id"]
    assert len(svc.store.intents(principal)) == 1
    s = _settings(b.intent(first["intent_id"]))
    # mock 不花钱，但用户选的上限是设置的一部分：页面看得到为什么 5 元与 6 元是两份不同的设置
    assert (s["clones_per_city"], s["max_cost"], s["currency"]) == (2, "5", "CNY")
    assert s["planner_kind"] == "mock" and s["service_attempt_limit"] is None


def test_a_restart_before_the_check_uses_the_saved_settings(tmp_path):
    clock = FakeClock()
    svc = start(tmp_path, clock=clock, default_clones_per_city=1)
    _, b = svc.person()
    view = b.prepare({"clones_per_city": 2}, cities=(K51,))  # 还在排队检查，worker 没有跑
    # 维护者改了默认值并重启 worker：还没检查的意图照样用创建时保存的那一份
    ctx = ServiceContext.open(config(tmp_path / "data", default_clones_per_city=3), clock=clock)
    restarted = Worker(ctx, sleep=lambda _s: None)
    restarted.run_pending()
    after = b.intent(view["intent_id"])
    assert after["stage"] == "awaiting_confirmation"
    assert (_settings(after)["cities"], _settings(after)["clones_per_city"]) == ([K51], 2)
    assert after["preflight"]["clones_per_city"] == 2
    assert b.submit(after).status_code == 202
    restarted.run_pending()
    done = b.intent(view["intent_id"])
    result = b.get(f"/runs/{done['run_id']}/result").json()
    assert len(result["clones"]) == 2 and {c["city_ref"] for c in result["clones"]} == {K51}
