"""一次运行的显示版本在生成运行前说明时绑定一次，之后不变（E3C 收尾复核 F3 遗留）。

* 确认开始时的再次核对、停下之后的恢复都不重绑：进度、结果与运行意图都继承这次运行已经确认的版本，之后发布的新资源不接管。
* 那份绑定缺了、坏了，或它记下的旧资源被删、被改：照实报缺失，不拿当前资源补（业务读取照常）。

全部离线（mock 规划器）。显示资源的登记表与冻结目录换到每个测试自己的临时目录（``publish``）。
"""

from __future__ import annotations

import json
import shutil

import pytest

from product.display import catalog
from tests.product_service.support import db_dump, ready_intent, start
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


def _bound_doc(svc, view):
    """这次运行的工作目录里，运行前说明的显示绑定文件。"""
    fp = view["preflight"]["preflight_fingerprint"]
    return (
        svc.ctx.run_dir(view["intent_id"])
        / "ws"
        / "workflow"
        / "display"
        / f"preflight-{fp[:16]}.json"
    )


def _release_v2(svc, tmp_path, publish, v1):
    _copy_source_with_marked_english(tmp_path / "src-v2")
    v2 = publish(svc.ctx.pack, root=tmp_path / "src-v2")
    assert v2 != v1 and catalog.current_version(svc.ctx.pack.fingerprint) == v2
    return v2


def _shown_with(b, run_id, intent_id, version):
    """结果、进度与运行意图（运行前说明那一部分）的英文全部按 ``version`` 显示，没有一句来自新资源。"""
    for kind, ident in (("result", run_id), ("progress", run_id), ("intent", intent_id)):
        env = display(b, kind, ident, "en").json()
        assert env["resource_version"] == version, kind
        fields = env["fields"]
        if kind == "intent":
            fields = [f for f in fields if f["pointer"].startswith("/preflight/")]
        else:
            assert env["status"] == "complete", kind
        assert fields and all(f["resource_version"] == version for f in fields), kind
        assert not any("[v2]" in (f["text"] or "") for f in fields), kind


def test_a_repeated_check_before_start_keeps_the_bound_version(tmp_path, publish):
    svc = start(tmp_path)
    v1 = publish(svc.ctx.pack)
    _, b = svc.person()
    ready = ready_intent(svc, b)
    path = _bound_doc(svc, ready)
    before = path.read_bytes()
    assert json.loads(before)["resource_version"] == v1
    _release_v2(svc, tmp_path, publish, v1)  # 只改英文，业务资料不变：运行前说明的身份不变
    assert b.submit(ready).status_code == 202  # 确认开始时工作流会再核对一次运行前说明
    svc.work()
    done = b.intent(ready["intent_id"])
    assert done["stage"] == "finished" and done["confirm_with"] is None
    assert path.read_bytes() == before  # 再次核对没有重绑
    db = db_dump(svc.store.path)
    _shown_with(b, done["run_id"], done["intent_id"], v1)
    assert db_dump(svc.store.path) == db  # 读取不写库、不补写


def test_a_resumed_run_keeps_the_bound_version(tmp_path, publish):
    svc = start(tmp_path)
    v1 = publish(svc.ctx.pack)
    _, b = svc.person()
    ready = ready_intent(svc, b)
    receipt = b.submit(ready).json()["receipt"]
    svc.worker.faults = {"life:halt_at_call:3:service_unavailable"}
    svc.work()
    svc.worker.faults = None
    paused = b.intent(ready["intent_id"])
    assert "resume" in paused["allowed_actions"]
    before = _bound_doc(svc, ready).read_bytes()
    _release_v2(svc, tmp_path, publish, v1)
    assert b.post(f"/runs/{receipt['run_id']}/resume").status_code == 202
    svc.work()
    done = b.intent(ready["intent_id"])
    assert done["stage"] == "finished", done
    assert _bound_doc(svc, ready).read_bytes() == before
    _shown_with(b, done["run_id"], done["intent_id"], v1)


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_a_lost_binding_stays_missing_and_is_not_rebound(tmp_path, publish, damage):
    svc = start(tmp_path)
    v1 = publish(svc.ctx.pack)
    _, b = svc.person()
    ready = ready_intent(svc, b)
    path = _bound_doc(svc, ready)
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("{not json", encoding="utf-8")
    _release_v2(svc, tmp_path, publish, v1)
    assert b.submit(ready).status_code == 202
    svc.work()
    done = b.intent(ready["intent_id"])
    assert done["stage"] == "finished"  # 显示缺失不挡运行
    # 再次核对没有拿当前资源补写这份绑定
    assert (
        (not path.exists())
        if damage == "missing"
        else path.read_text(encoding="utf-8") == "{not json"
    )
    result = display(b, "result", done["run_id"], "en").json()
    assert result["source"] == b.get(f"/runs/{done['run_id']}/result").json()
    assert result["status"] == "unavailable" and result["resource_version"] is None
    assert all(f["text"] is None and f["missing_reason"] for f in result["fields"])
    zh = display(b, "result", done["run_id"], "zh").json()
    assert zh["status"] == "complete"  # 中文照原消息
    # 进度与运行意图用服务库里这次运行确认时保存的绑定（v1），也不换成新资源
    for kind, ident, part in (
        ("progress", done["run_id"], "/"),
        ("intent", done["intent_id"], "/preflight/"),
    ):
        env = display(b, kind, ident, "en").json()
        assert env["resource_version"] == v1, kind
        bound = [f for f in env["fields"] if f["pointer"].startswith(part)]
        assert bound and not any("[v2]" in (f["text"] or "") for f in bound), kind


@pytest.mark.parametrize("damage", ["deleted", "tampered"])
def test_old_resources_gone_or_tampered_are_reported_not_replaced(tmp_path, publish, damage):
    svc = start(tmp_path)
    v1 = publish(svc.ctx.pack)
    _, b = svc.person()
    ready = ready_intent(svc, b)
    _release_v2(svc, tmp_path, publish, v1)
    assert b.submit(ready).status_code == 202
    svc.work()
    done = b.intent(ready["intent_id"])
    assert done["stage"] == "finished"
    old = catalog.FROZEN / svc.ctx.pack.fingerprint / v1
    if damage == "deleted":
        shutil.rmtree(old)
    else:
        body = old / "resources.json"
        body.write_text(
            body.read_text(encoding="utf-8").replace('"en"', '"en" ', 1), encoding="utf-8"
        )
    catalog._load_frozen.cache_clear()
    reason = "resources_unavailable" if damage == "deleted" else "resources_invalid"
    for kind in ("result", "progress"):
        env = display(b, kind, done["run_id"], "en").json()
        assert env["resource_version"] == v1, kind  # 仍指向它绑定的版本，不回退到 v2
        assert env["status"] == "unavailable", kind
        assert all(f["missing_reason"] == reason for f in env["fields"]), kind
        assert b.get(f"/runs/{done['run_id']}/{kind}").status_code == 200  # 业务读取照常
