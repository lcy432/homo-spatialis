"""资料构建流程（LangGraph）：中断后恢复、产物按身份复用、冻结后不重算、checkpoint 只存引用、外部追踪守卫。

用的是 ``tests/fixtures/intake`` 的真实小样本（芝加哥都会区）；全程离线。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from product.realdata import build, geo, sources
from product.realdata.build import STAGES, InjectedInterruptError, RawChangedError
from product.realdata.fetch import read_cached
from tests.product.realdata.support import CACHE, block_network, fixture_ctx
from workflows.data_build import DataBuildRefusedError, DataBuildRuntime
from workflows.runtime import TRACING_ENV, WorkflowRefusedError


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    block_network(monkeypatch)


def _identities(status):
    return {s["stage"]: s.get("identity") for s in status["stages"]}


def _paths(status):
    return {s["stage"]: s.get("path") for s in status["stages"]}


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
    menus_resumed = (work / _paths(done)["catalog"] / "menus.jsonl.gz").read_bytes()
    menus_direct = (tmp_path / "direct" / direct["catalog"].path / "menus.jsonl.gz").read_bytes()
    assert gzip.decompress(menus_resumed) == gzip.decompress(menus_direct)  # 恢复不改变菜单


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


# --------------------------------------------------------------------------- F1：恢复只读获取阶段记下的那一份原始文件


def _private_cache(tmp_path: Path) -> Path:
    dst = tmp_path / "cache"
    shutil.copytree(CACHE, dst)
    return dst


def _refresh(cache: Path, req, content: bytes) -> str:
    """改变缓存里的原始文件，并写出合法的元数据（SHA-256 与大小都对得上），模拟缓存被重新获取。返回新内容的 SHA-256。"""
    base = cache / req.source_id / req.key[:16]
    base.with_suffix(".bin").write_bytes(content)
    meta = json.loads(base.with_suffix(".meta.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(content).hexdigest()
    meta.update(sha256=digest, bytes=len(content))
    base.with_suffix(".meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return digest


def _goods_records(work: Path, status) -> list[dict]:
    path = next(s["path"] for s in status["stages"] if s["stage"] == "standardize")
    with gzip.open(work / path / "records_goods.jsonl.gz", "rt", encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def test_refreshed_cache_cannot_feed_new_content_into_an_old_build(tmp_path):
    cache, work, db = _private_cache(tmp_path), tmp_path / "work", tmp_path / "flow.sqlite"
    with DataBuildRuntime(db, fixture_ctx(work, cache=cache)) as rt:
        old = rt.run("t1", stop_after="fetch")
    req = sources.energystar_request("dishwasher")
    rows = json.loads(read_cached(req, cache).content)
    new_sha = _refresh(cache, req, json.dumps(rows[1:]).encode("utf-8"))
    with DataBuildRuntime(db, fixture_ctx(work, cache=cache)) as rt:
        with pytest.raises(RawChangedError, match="energystar:dishwasher"):
            rt.run("t1")
        st = rt.status("t1")
    assert st["status"] == "paused" and st["next"] == ["standardize"]
    assert not list(work.glob("standardize/*/done.json"))  # 没有产生“新内容 + 旧指纹”的标准化产物
    # 新资料走新构建：获取阶段的身份变了，记录里写的是新内容的指纹
    with DataBuildRuntime(db, fixture_ctx(work, cache=cache)) as rt:
        fresh = rt.run("t2")
    ids_old, ids_new = _identities(old), _identities(fresh)
    assert ids_new["fetch"] != ids_old["fetch"] and fresh["status"] == "frozen"
    dish = [r for r in _goods_records(work, fresh) if r["attributes"]["category"] == "dishwasher"]
    assert len(dish) == len(rows) - 1 and {r["source"]["raw_sha256"] for r in dish} == {new_sha}


def test_geography_files_are_bound_to_the_fetched_fingerprint_too(tmp_path):
    cache, work, db = _private_cache(tmp_path), tmp_path / "work", tmp_path / "flow.sqlite"
    with DataBuildRuntime(db, fixture_ctx(work, cache=cache)) as rt:
        rt.run("t1", stop_after="fetch")
    req = geo.acs_geos_request(2024)
    text = read_cached(req, cache).content.decode("latin-1")
    _refresh(cache, req, text.replace("Chicago", "Chicagoland", 1).encode("latin-1"))
    with (
        DataBuildRuntime(db, fixture_ctx(work, cache=cache)) as rt,
        pytest.raises(RawChangedError, match="acs_geos"),
    ):
        rt.run("t1")
