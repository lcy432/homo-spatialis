"""资料构建流程（LangGraph）：中断后恢复、产物按身份复用、冻结后不重算、checkpoint 只存引用、外部追踪守卫。

用的是 ``tests/fixtures/intake`` 的真实小样本（芝加哥都会区）；全程离线。
"""

from __future__ import annotations

import pytest

from product.realdata import build
from product.realdata.build import STAGES, InjectedInterruptError
from tests.product.realdata.support import block_network, fixture_ctx
from workflows.data_build import DataBuildRefusedError, DataBuildRuntime
from workflows.runtime import TRACING_ENV, WorkflowRefusedError


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    block_network(monkeypatch)


def _identities(status):
    return {s["stage"]: s.get("identity") for s in status["stages"]}


def test_interrupted_build_resumes_from_the_failed_stage_and_matches_the_direct_build(tmp_path):
    work, db = tmp_path / "work", tmp_path / "flow.sqlite"
    with DataBuildRuntime(db, fixture_ctx(work, fail_after={"standardize": 1})) as rt:
        with pytest.raises(InjectedInterruptError):
            rt.run("t1")
        st = rt.status("t1")
    assert st["status"] == "paused" and st["next"] == ["standardize"]
    assert [s["state"] for s in st["stages"]][:3] == ["done", "done", "pending"]
    partial = [d for d in (work / "standardize").iterdir() if not (d / "done.json").exists()]
    assert len(partial) == 1  # 中断留下的半成品：恢复时清掉重做，不当成完成
    (partial[0] / "stale.txt").write_text("半成品", encoding="utf-8")

    # 新的运行对象（相当于重启进程）：已完成的阶段不重跑，从标准化接着跑到冻结
    calls = []
    real_fetch = build.stage_fetch

    def counting_fetch(*a, **k):
        calls.append("fetch")
        return real_fetch(*a, **k)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build, "stage_fetch", counting_fetch)
        with DataBuildRuntime(db, fixture_ctx(work)) as rt:
            done = rt.run("t1")
    assert done["status"] == "frozen" and calls == []
    assert (partial[0] / "done.json").exists() and not (partial[0] / "stale.txt").exists()

    direct = build.run_all(fixture_ctx(tmp_path / "direct"))
    assert _identities(done)["freeze"] == direct["freeze"].identity  # 经过图与不经过图是同一个构建


def test_checkpoint_holds_only_stage_references(tmp_path):
    with DataBuildRuntime(tmp_path / "flow.sqlite", fixture_ctx(tmp_path / "work")) as rt:
        rt.run("t1")
        values = rt.graph.get_state({"configurable": {"thread_id": "t1"}}).values
    assert set(values) <= {"config_identity", "refs", "stage", "status"}
    assert set(values["refs"]) == set(STAGES)
    assert all(set(r) == {"stage", "identity", "path"} for r in values["refs"].values())


def test_stop_after_a_stage_then_continue(tmp_path):
    with DataBuildRuntime(tmp_path / "flow.sqlite", fixture_ctx(tmp_path / "work")) as rt:
        st = rt.run("t1", stop_after="map")
        assert st["status"] == "paused" and st["next"] == ["catalog"]
        assert rt.run("t1")["status"] == "frozen"


def test_frozen_flow_returns_its_result_without_refitting_or_resampling(tmp_path, monkeypatch):
    with DataBuildRuntime(tmp_path / "flow.sqlite", fixture_ctx(tmp_path / "work")) as rt:
        first = rt.run("t1")

        def refuse(*_a, **_k):
            raise AssertionError("冻结之后不应当再跑任何阶段")

        for name in (
            "stage_configure",
            "stage_fetch",
            "stage_standardize",
            "stage_map",
            "stage_catalog",
            "stage_check",
            "stage_freeze",
        ):
            monkeypatch.setattr(build, name, refuse)
        again = rt.run("t1")
        assert rt.frozen_db("t1").exists()
    assert again["status"] == "frozen" and _identities(again) == _identities(first)


def test_a_new_flow_on_the_same_work_directory_reuses_every_artifact(tmp_path):
    work = tmp_path / "work"
    with DataBuildRuntime(tmp_path / "flow.sqlite", fixture_ctx(work)) as rt:
        first = rt.run("t1")
        stamps = {p: p.stat().st_mtime_ns for p in work.rglob("done.json")}
        second = rt.run("t2")
    assert _identities(second) == _identities(first)
    assert {
        p: p.stat().st_mtime_ns for p in work.rglob("done.json")
    } == stamps  # 没有重写任何阶段产物


def test_changed_config_cannot_continue_an_old_flow(tmp_path):
    work, db = tmp_path / "work", tmp_path / "flow.sqlite"
    with DataBuildRuntime(db, fixture_ctx(work)) as rt:
        rt.run("t1", stop_after="fetch")
    ctx = fixture_ctx(work)
    ctx.config = ctx.config.model_copy(
        update={"menus": ctx.config.menus.model_copy(update={"env_repeats": 1})}
    )
    with DataBuildRuntime(db, ctx) as rt:
        with pytest.raises(DataBuildRefusedError) as e:
            rt.run("t1")
        assert e.value.code == "config_changed"
        assert (
            rt.default_thread()
            != DataBuildRuntime(tmp_path / "other.sqlite", fixture_ctx(work)).default_thread()
        )


def test_default_flow_changes_when_a_code_version_changes(tmp_path, monkeypatch):
    rt = DataBuildRuntime(tmp_path / "flow.sqlite", fixture_ctx(tmp_path / "work"))
    before = rt.default_thread()
    monkeypatch.setattr(build, "STANDARDIZE_VERSION", "test-bump")
    assert rt.default_thread() != before
    rt.close()


@pytest.mark.parametrize("name", TRACING_ENV)
def test_external_tracing_must_be_off(tmp_path, monkeypatch, name):
    monkeypatch.setenv(name, "true")
    with pytest.raises(WorkflowRefusedError) as e:
        DataBuildRuntime(tmp_path / "flow.sqlite", fixture_ctx(tmp_path / "work"))
    assert e.value.code == "external_tracing_enabled"
    assert not (tmp_path / "flow.sqlite").exists()
