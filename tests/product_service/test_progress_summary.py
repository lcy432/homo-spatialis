"""运行意图的进度摘要（``RunIntentView.progress_summary``，DECISIONS/0049）：一句当前状态、更新时间与进度条。

* 只读：读取意图（含英文显示）不写库；检查阶段还没有运行编号也有摘要。
* 阶段：检查、排队、模拟中（已结算 / 计划的分身年度数，来自 E1 的进度快照）、整理结果、完成（只在结果可以读时）、需要处理，以及取消、
  删除、过期。不按调用次数或时间估计；提前出局、停下之后没有经历的年份不算进已结算；完成不等于所有分身都是 C。
* 中英文：摘要的一句话走显示附层，换语言不改阶段与计数。
"""

from __future__ import annotations

import json

import pytest

from product.display import catalog
from product.service.views import progress_summary
from tests.product_service.support import db_dump, ready_intent, start

T0 = "2026-10-25T08:00:00Z"


@pytest.fixture
def publish(tmp_path, monkeypatch):
    """登记表与冻结目录换到这个测试的临时目录，冻结当前的显示资源（与 ``test_display_service`` 同样的夹具）。"""
    monkeypatch.setattr(catalog, "FROZEN", tmp_path / "display-frozen")
    monkeypatch.setattr(catalog, "REGISTRY", tmp_path / "display-registry.yaml")

    def run(pack):
        version = catalog.freeze(pack.fingerprint, pack.body.pack_id)
        catalog.register(pack.fingerprint, pack.body.pack_id, version)
        return version

    yield run
    catalog._load_frozen.cache_clear()


def _row(svc, **over):
    """一份服务库里的意图行（只用到摘要读的几列）。"""
    base = {
        "stage": "checking",
        "updated_at": T0,
        "start_authorization": None,
        "progress_body": None,
        "execution_body": svc.ctx.default_spec.model_dump_json(),
        "resume_allowed": 0,
        "result_body": None,
        "run_id": None,
    }
    base.update(over)
    return base


def _snap(
    svc,
    years: list[int],
    stage: str = "running",
    at: str = "2026-10-25T08:05:00Z",
    settled: int | None = None,
) -> str:
    """一份进度快照：``years`` 是已经有结局记录的分身各自已结算的年数；``settled`` 是 E1 归档里已结算的分身年度总数（进行中的分身也
    算到它过完的那一年；不给就是旧快照，没有这一项）。"""
    clones = [
        {
            "clone_ref": f"c{i}",
            "city_ref": "X",
            "status": "ok",
            "status_zh": "x",
            "years_committed": y,
        }
        for i, y in enumerate(years)
    ]
    body = {"stage": stage, "clones": clones, "updated_at": at}
    if settled is not None:
        spec = svc.ctx.default_spec
        body |= {"clone_years_settled": settled, "clone_years_planned": spec.clones * spec.years}
    return json.dumps(body)


def test_each_phase_has_one_sentence_and_the_right_bar(tmp_path):
    svc = start(tmp_path)
    spec = svc.ctx.default_spec
    planned = spec.clones * spec.years
    pending = json.dumps({"state": "pending", "consent_fingerprint": "a" * 64})
    cases = [
        (dict(stage="checking"), "checking", "indeterminate"),
        (dict(stage="checking", start_authorization=pending), "checking", "indeterminate"),
        (
            dict(stage="awaiting_confirmation", start_authorization=pending),
            "checking",
            "indeterminate",
        ),
        (dict(stage="awaiting_confirmation"), "needs_attention", "indeterminate"),
        (dict(stage="blocked"), "needs_attention", "indeterminate"),
        (dict(stage="queued", run_id="r1"), "queued", "indeterminate"),
        (dict(stage="running", run_id="r1"), "simulating", "indeterminate"),  # 第一份快照还没写下
        (
            dict(stage="running", run_id="r1", progress_body=_snap(svc, [1, 0])),
            "simulating",
            "clone_years",
        ),
        (  # 进行中的分身还没有结局记录：E1 归档里已结算的年度数照样算进去
            dict(stage="running", run_id="r1", progress_body=_snap(svc, [], settled=2)),
            "simulating",
            "clone_years",
        ),
        (
            dict(stage="running", run_id="r1", progress_body=_snap(svc, [3, 3], "completed")),
            "assembling_result",
            "indeterminate",
        ),
        (
            dict(stage="needs_attention", run_id="r1", progress_body=_snap(svc, [2, 1], "halted")),
            "needs_attention",
            "clone_years",
        ),
        (
            dict(
                stage="finished",
                run_id="r1",
                progress_body=_snap(svc, [3, 1], "completed"),
                result_body="{}",
            ),
            "finished",
            "finished",
        ),
        (
            dict(stage="finished", run_id="r1", progress_body=_snap(svc, [3, 3], "completed")),
            "needs_attention",
            "indeterminate",
        ),
        (dict(stage="cancelled"), "cancelled", "indeterminate"),
        (dict(stage="deleting"), "deleted", "indeterminate"),
        (dict(stage="deleted"), "deleted", "indeterminate"),
        (dict(stage="expired"), "expired", "indeterminate"),
    ]
    for over, phase, mode in cases:
        s = progress_summary(_row(svc, **over), started=False, start_allowed=True)
        assert (s.phase, s.mode) == (phase, mode), over
        assert s.sentence_zh and "\n" not in s.sentence_zh
    sim = progress_summary(
        _row(svc, stage="running", run_id="r1", progress_body=_snap(svc, [1, 0])),
        started=True,
        start_allowed=True,
    )
    assert (sim.settled_clone_years, sim.planned_clone_years) == (1, planned)
    assert (
        sim.sentence_zh == f"已结算 1/{planned} 个分身年度"
        and sim.updated_at == "2026-10-25T08:05:00Z"
    )
    # 一个分身第 1 年出局：之后没有经历的年份不算；结束且结果可读才是完成，完成不等于全部完成
    done = progress_summary(
        _row(
            svc,
            stage="finished",
            run_id="r1",
            progress_body=_snap(svc, [3, 1], "completed"),
            result_body="{}",
        ),
        started=True,
        start_allowed=True,
    )
    assert (done.settled_clone_years, done.planned_clone_years) == (
        4,
        planned,
    ) and done.phase == "finished"
    resumed = progress_summary(
        _row(svc, stage="queued", run_id="r1"), started=True, start_allowed=True
    )
    assert resumed.sentence_zh == "已经开始过，排队等待恢复"


def test_counts_come_from_the_e1_archive_and_old_snapshots_are_not_guessed(tmp_path):
    svc = start(tmp_path)
    planned = svc.ctx.default_spec.clones * svc.ctx.default_spec.years
    s = progress_summary(
        _row(svc, stage="running", run_id="r1", progress_body=_snap(svc, [], settled=2)),
        started=True,
        start_allowed=True,
    )
    assert (s.settled_clone_years, s.planned_clone_years) == (2, planned)
    # 旧版本写下的快照：只有一个分身有结局记录，进行中的那个过了几年不知道——不给计数，不拿下界冒充
    old = progress_summary(
        _row(svc, stage="running", run_id="r1", progress_body=_snap(svc, [3])),
        started=True,
        start_allowed=True,
    )
    assert (old.mode, old.settled_clone_years) == ("indeterminate", None)
    assert old.sentence_zh == "模拟进行中（这份进度快照没有记下已结算的分身年度数）"


def test_a_normal_run_reads_truthfully_and_reading_writes_nothing(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _principal, b = svc.person()
    view = ready_intent(svc, b)
    assert view["progress_summary"]["phase"] == "needs_attention"  # 旧流程：等你确认开始
    assert view["progress_summary"]["sentence_zh"] == "检查通过，等你确认开始"
    r = b.submit(view)
    assert r.status_code == 202
    queued = b.intent(view["intent_id"])["progress_summary"]
    assert (queued["phase"], queued["mode"]) == ("queued", "indeterminate")
    svc.work()
    before = db_dump(svc.store.path)
    done = b.intent(view["intent_id"])
    en = b.get(
        "/display", params={"kind": "intent", "id": view["intent_id"], "locale": "en"}
    ).json()
    assert db_dump(svc.store.path) == before  # 读取与英文显示不写库
    s = done["progress_summary"]
    spec = svc.ctx.default_spec
    assert s["phase"] == "finished" and s["mode"] == "finished"
    assert s["planned_clone_years"] == spec.clones * spec.years
    fields = {f["pointer"]: f for f in en["fields"]}
    sentence = fields["/progress_summary/sentence_zh"]
    assert sentence["status"] == "provided" and sentence["text"].startswith("Results are ready")


def test_a_stopped_run_counts_only_settled_years(tmp_path):
    svc = start(tmp_path)
    _principal, b = svc.person()
    view = ready_intent(svc, b)
    assert b.submit(view).status_code == 202
    svc.worker.faults = {"life:halt_at_call:3:service_unavailable"}
    svc.work()
    svc.worker.faults = None
    s = b.intent(view["intent_id"])["progress_summary"]
    assert s["phase"] == "needs_attention" and s["mode"] == "clone_years"
    assert 0 <= s["settled_clone_years"] < s["planned_clone_years"]
    # 已结算 = E1 归档里执行并结算过的年度行动条数（进行中的分身也算到它过完的那一年）
    from orchestration.life_archive import RunArchive

    row = svc.store.intent_row(view["intent_id"])
    run_dir = svc.ctx.run_dir(view["intent_id"]) / "ws" / "archive" / row["run_id"]
    assert (
        s["settled_clone_years"] == len(RunArchive(run_dir, create=False).records("accepted")) > 0
    )
    assert "可以恢复" in s["sentence_zh"]
