"""LangGraph 外层流程接入真实 E3 / E1（零网络、零费用；规划器是资料包声明的离线 mock）。

对照（直接路径 vs LangGraph 路径）的规则见 ``runs/2026-10-18-langgraph-e3-wiring/expected_differences.md``。崩溃都是真的结束进程
（``os._exit``）；E1 的停下（服务失败、发送不确定、预算）用包住 mock 的离线故障注入，不花钱。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from common.product_contracts import ProductResult
from workflows.e3_adapters import E3Context, E3Workspace, RunRequest, e3_services, seed_example
from workflows.ports import NotWiredError, WorkflowServices
from workflows.runtime import WorkflowRefusedError, WorkflowRuntime
from workflows.synthetic import SyntheticWorld

from .e3_support import cli_e3, compare, direct, key_for, tree, via_workflow

SENTINELS = ("MockPlannerError", "受控的意外异常", "Traceback", "C:/Users", "C:\\Users")


def _calls(ws: E3Workspace) -> list[dict]:
    path = ws.root / "workflow/planner_invocations.jsonl"
    return (
        [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        if path.is_file()
        else []
    )


def _progress(ws: E3Workspace, run_id: str) -> dict:
    return json.loads((ws.root / "archive" / run_id / "progress.json").read_text(encoding="utf-8"))


def _start(
    tmp: Path, example: str = "normal", *, faults: set[str] | None = None, name: str | None = None
):
    ws = E3Workspace.prepare(tmp / "ws")
    profile, inputs = seed_example(ws, example, name=name)
    rt = WorkflowRuntime(tmp / "wf.db", e3_services(ws, faults=faults))
    view = rt.start(
        workflow_id="W", profile=profile, inputs=inputs, idempotency_key=key_for(example, name)
    )
    return ws, rt, view


# ---------- 10. 直接路径与 LangGraph 路径逐项相同 ----------


@pytest.mark.parametrize("example", ["normal", "mixed", "all_exit", "paused"])
def test_direct_and_langgraph_paths_are_identical(tmp_path, example):
    s = direct(tmp_path / "direct", example)
    ws, view = via_workflow(tmp_path / "wf", example)
    assert view["stage"] == "delivered"
    r = compare(s, ws, view)
    assert r["all_same"], {k: v for k, v in r.items() if k != "workflow_only_files"}
    assert r["archive_files"] > 0 and r["compiled_files"] == 4


def test_service_halt_then_human_retry_ends_with_the_same_life_as_the_direct_path(tmp_path):
    s = direct(tmp_path / "direct", "normal")
    ws, rt, view = _start(
        tmp_path / "wf", faults={"life:halt_at_call:3:service_unavailable", "count_calls"}
    )
    with rt:
        stopped = rt.confirm("W", view["confirm_with"])
        assert (
            stopped["stage"] == "needs_attention"
            and stopped["attention"]["code"] == "service_unavailable"
        )
        run_id = stopped["run_id"]
        assert (
            _progress(ws, run_id)["status"] == "halted" and _progress(ws, run_id)["calls_used"] == 2
        )
        assert stopped["progress"]["stage"] == "halted" and stopped["result"] is None
        assert rt.resume("W")["stage"] == "needs_attention"  # 恢复不会自己重试
        assert len(_calls(ws)) == 3
        done = rt.resolve("W", "retry")
    assert done["stage"] == "delivered"
    r = compare(s, ws, done)
    assert r["all_same"], {k: v for k, v in r.items() if k != "workflow_only_files"}
    # 第 3 次调用第一次没有回复（服务失败），人工重试后由 E1 的恢复入口重新请求；第 1、2 次的回复复用，不再请求
    ids = [c["request_id"] for c in _calls(ws)]
    assert len(ids) == 7 and ids[2] == ids[3] and len(set(ids)) == 6


# ---------- 1. 跨进程：开始 → 换进程查看 → 换进程确认 → 看结果 ----------


def test_e3_story_across_processes(tmp_path):
    ws = E3Workspace.prepare(tmp_path / "ws")
    profile, inputs = seed_example(ws, "normal")
    code, view = cli_e3(
        tmp_path,
        "start",
        "--workflow-id",
        "W1",
        "--profile",
        profile.ref_id,
        "--inputs",
        inputs.ref_id,
    )
    assert code == 0 and view["stage"] == "awaiting_confirmation"
    assert view["preflight"]["public"]["status"] == "ready"  # E3 的 ProductPreflight 原样展示
    assert not (ws.root / "registry").exists() and not (ws.root / "archive").exists()
    _, again = cli_e3(tmp_path, "status", "--workflow-id", "W1")
    assert again["confirm_with"] == view["confirm_with"]
    code, done = cli_e3(
        tmp_path, "confirm", "--workflow-id", "W1", "--preflight", view["confirm_with"]
    )
    assert code == 0 and done["stage"] == "delivered"
    result = ProductResult.model_validate(done["result"])
    assert result.evaluation_status == "generated" and result.run_id == done["run_id"]
    assert [(c.planned, c.complete, c.exits, c.missing) for c in result.cities] == [
        (1, 1, 0, 0),
        (1, 1, 0, 0),
    ]
    before = tree(ws.root / "archive")
    for cmd in ("status", "resume", "status"):
        _, v = cli_e3(tmp_path, cmd, "--workflow-id", "W1")
        assert v["result"] == done["result"]
    assert tree(ws.root / "archive") == before


# ---------- 2. 未确认、拒绝、过期确认都不启动 ----------


def test_nothing_reaches_e1_before_a_valid_confirmation(tmp_path):
    ws, rt, _view = _start(tmp_path)
    with rt:
        assert rt.confirm("W", "0" * 64)["notice"]["code"] == "confirmation_stale"
        assert rt.decline("W")["stage"] == "declined"
    assert not (ws.root / "registry").exists() and not (ws.root / "archive").exists()


def test_changed_inputs_after_the_check_expire_the_confirmation(tmp_path):
    ws, rt, view = _start(tmp_path)
    with rt:
        # 资料包在预检查之后变了（这里只改了一行说明文字）：登记前的再检查发现，旧确认作废
        pack = ws.root / "configs/product/e3a_synthetic_pack.yaml"
        original = pack.read_text(encoding="utf-8")
        assert original.count("readable_time_note: ") == 1
        changed = original.replace(
            "readable_time_note: ", "readable_time_note: 【资料包已更新】", 1
        )
        pack.write_text(changed, encoding="utf-8", newline="\n")
        expired = rt.confirm("W", view["confirm_with"])
        assert expired["stage"] == "blocked" and expired["notice"]["code"] == "inputs_changed"
        assert expired["preflight"]["block"]["code"] == "reference_stale"
        assert not (ws.root / "registry").exists() and not (ws.root / "archive").exists()
        # 恢复资料包后用同一份输入重新检查：得到原来那份检查身份，再确认才运行
        pack.write_text(original, encoding="utf-8", newline="\n")
        revised = rt.revise("W", inputs=ws.request_ref("normal"))
        assert (
            revised["stage"] == "awaiting_confirmation"
            and revised["confirm_with"] == view["confirm_with"]
        )
        assert rt.confirm("W", revised["confirm_with"])["stage"] == "delivered"


# ---------- 3. 幂等提交 ----------


def test_same_key_same_inputs_is_one_run_and_a_reused_key_is_refused_by_e3(tmp_path):
    ws = E3Workspace.prepare(tmp_path / "ws")
    p1, i1 = seed_example(ws, "normal")
    p2, i2 = seed_example(ws, "paused")
    with WorkflowRuntime(tmp_path / "wf.db", e3_services(ws)) as rt:
        a = rt.start(workflow_id="A", profile=p1, inputs=i1, idempotency_key="click-0001")
        a = rt.confirm("A", a["confirm_with"])
        b = rt.start(workflow_id="B", profile=p1, inputs=i1, idempotency_key="click-0001")
        b = rt.confirm("B", b["confirm_with"])
        assert a["run_id"] == b["run_id"] and b["stage"] == "delivered"
        c = rt.start(workflow_id="C", profile=p2, inputs=i2, idempotency_key="click-0001")
        c = rt.confirm("C", c["confirm_with"])
        assert c["stage"] == "needs_attention" and c["attention"]["code"] == "identity_conflict"
        assert c["run_id"] is None
    # 同一个键的回执文件记最后一次：E3 拒绝（之前两次是 accepted 与 duplicate_same_run）
    receipts = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (ws.root / "workflow/receipts").glob("*.json")
    ]
    assert [r["status"] for r in receipts] == ["rejected"]
    assert "idempotency_key_reused" in {x["code"] for x in receipts[0]["reasons"]}
    assert sorted(p.name for p in (ws.root / "archive").iterdir()) == [a["run_id"]]
    registry = json.loads((ws.root / "registry/submissions.json").read_text(encoding="utf-8"))
    assert list(registry) == ["click-0001"] and registry["click-0001"]["run_id"] == a["run_id"]


def test_a_key_that_breaks_the_e3_rule_is_refused_before_the_workflow_exists(tmp_path):
    ws = E3Workspace.prepare(tmp_path / "ws")
    profile, inputs = seed_example(ws, "normal")
    with WorkflowRuntime(tmp_path / "wf.db", e3_services(ws)) as rt:
        with pytest.raises(WorkflowRefusedError) as e:
            rt.start(workflow_id="W", profile=profile, inputs=inputs, idempotency_key="short")
        assert e.value.code == "idempotency_key_invalid"
        with pytest.raises(WorkflowRefusedError) as e:
            rt.status("W")
        assert e.value.code == "workflow_not_found"  # 没有建流程
    assert not (ws.root / "workflow/preflights").exists() and not (ws.root / "registry").exists()


# ---------- 4–6. 三种崩溃位置（真的结束进程） ----------


def test_crash_after_e3_registered_the_run(tmp_path):
    ws = E3Workspace.prepare(tmp_path / "ws")
    profile, inputs = seed_example(ws, "normal")
    _, view = cli_e3(
        tmp_path,
        "start",
        "--workflow-id",
        "W",
        "--profile",
        profile.ref_id,
        "--inputs",
        inputs.ref_id,
    )
    code, out = cli_e3(
        tmp_path,
        "confirm",
        "--workflow-id",
        "W",
        "--preflight",
        view["confirm_with"],
        fault="submit:after_register",
    )
    assert code == 51 and out is None
    registry = json.loads((ws.root / "registry/submissions.json").read_text(encoding="utf-8"))
    (entry,) = registry.values()
    assert not (ws.root / "workflow/runs").exists() and not (ws.root / "archive").exists()
    _, mid = cli_e3(tmp_path, "status", "--workflow-id", "W")
    assert mid["stage"] == "submitting" and mid["run_id"] is None
    _, done = cli_e3(tmp_path, "resume", "--workflow-id", "W")
    assert done["stage"] == "delivered" and done["run_id"] == entry["run_id"]
    assert (
        json.loads((ws.root / "registry/submissions.json").read_text(encoding="utf-8")) == registry
    )
    assert [p.name for p in (ws.root / "archive").iterdir()] == [entry["run_id"]]


def test_crash_inside_e1_resumes_through_the_e1_resume_entry(tmp_path):
    s = direct(tmp_path / "direct", "normal")
    ws = E3Workspace.prepare(tmp_path / "wf" / "ws")
    profile, inputs = seed_example(ws, "normal")
    w = tmp_path / "wf"
    _, view = cli_e3(
        w,
        "start",
        "--workflow-id",
        "W",
        "--profile",
        profile.ref_id,
        "--inputs",
        inputs.ref_id,
        "--idempotency-key",
        key_for("normal"),
    )
    code, _ = cli_e3(
        w,
        "confirm",
        "--workflow-id",
        "W",
        "--preflight",
        view["confirm_with"],
        fault="life:crash_at_call:3,count_calls",
    )
    assert code == 52
    first = _calls(ws)
    run_id = json.loads((ws.root / "registry/submissions.json").read_text(encoding="utf-8"))[
        key_for("normal")
    ]["run_id"]
    assert len(first) == 3 and _progress(ws, run_id)["status"] == "running"
    archived_before = {
        json.loads(x)["request_id"]
        for x in (ws.root / "archive" / run_id / "calls.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    }
    assert len(archived_before) == 2
    code, done = cli_e3(w, "resume", "--workflow-id", "W", fault="count_calls")
    assert code == 0 and done["stage"] == "delivered"
    second = [c for c in _calls(ws) if c["pid"] != first[0]["pid"]]
    assert {c["request_id"] for c in second}.isdisjoint(
        archived_before
    )  # 已保存回复的调用没有再请求
    assert (
        len(second) == 4 and second[0]["request_id"] == first[2]["request_id"]
    )  # 被打断的那一次没有回复，重新请求
    r = compare(s, ws, done)
    assert r["all_same"], {k: v for k, v in r.items() if k != "workflow_only_files"}


def test_life_finished_before_the_outer_checkpoint_is_only_read(tmp_path):
    ws = E3Workspace.prepare(tmp_path / "ws")
    profile, inputs = seed_example(ws, "normal")
    _, view = cli_e3(
        tmp_path,
        "start",
        "--workflow-id",
        "W",
        "--profile",
        profile.ref_id,
        "--inputs",
        inputs.ref_id,
    )
    code, _ = cli_e3(
        tmp_path,
        "confirm",
        "--workflow-id",
        "W",
        "--preflight",
        view["confirm_with"],
        fault="life:after_complete",
    )
    assert code == 53
    (run_id,) = [p.name for p in (ws.root / "archive").iterdir()]
    assert _progress(ws, run_id)["status"] == "completed"
    _, mid = cli_e3(tmp_path, "status", "--workflow-id", "W")
    assert mid["stage"] == "running"
    before = tree(ws.root / "archive")
    code, done = cli_e3(tmp_path, "resume", "--workflow-id", "W", fault="count_calls")
    assert (
        code == 0
        and done["stage"] == "delivered"
        and done["result"]["evaluation_status"] == "generated"
    )
    assert tree(ws.root / "archive") == before and _calls(ws) == []  # 没有再模拟，也没有调用规划器


# ---------- 9. 发送不确定、预算阻断：外层重试不会绕过 ----------


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("uncertain_send", "uncertain_send"),
        ("budget", "budget_blocked"),
        ("credentials", "run_halted"),
    ],
)
def test_e1_halts_that_need_a_person_survive_retries(tmp_path, kind, code):
    ws, rt, view = _start(tmp_path, faults={f"life:halt_at_call:2:{kind}", "count_calls"})
    with rt:
        out = rt.confirm("W", view["confirm_with"])
        run_id = out["run_id"]
        before = tree(ws.root / "archive")
        for _ in range(3):
            assert out["stage"] == "needs_attention" and out["attention"]["code"] == code
            out = rt.resolve("W", "retry")
        assert len(_calls(ws)) == 2 and tree(ws.root / "archive") == before
        assert _progress(ws, run_id)["halt_kind"] == kind
        assert rt.resolve("W", "abandon")["stage"] == "abandoned"
    assert tree(ws.root / "archive") == before


# ---------- 8. 归档被改、配置变了、映射冲突、只建了一半：执行前拒绝，原记录不改 ----------


def _halted(tmp_path):
    ws, rt, view = _start(tmp_path, faults={"life:halt_at_call:3:service_unavailable"})
    out = rt.confirm("W", view["confirm_with"])
    assert out["attention"]["code"] == "service_unavailable"
    return ws, rt, out["run_id"]


def test_tampered_e1_archive_is_refused_without_rewriting_it(tmp_path):
    ws, rt, run_id = _halted(tmp_path)
    accepted = ws.root / "archive" / run_id / "accepted.jsonl"
    accepted.write_text(
        accepted.read_text(encoding="utf-8").replace('"quantity":1.0', '"quantity":2.0', 1),
        encoding="utf-8",
        newline="\n",
    )
    before = tree(ws.root / "archive")
    with rt:
        out = rt.resolve("W", "retry")
    assert out["stage"] == "needs_attention" and out["attention"]["code"] == "archive_conflict"
    assert tree(ws.root / "archive") == before


def test_a_report_changed_after_e1_finished_is_not_projected(tmp_path):
    ws = E3Workspace.prepare(tmp_path / "ws")
    profile, inputs = seed_example(ws, "normal")
    _, view = cli_e3(
        tmp_path,
        "start",
        "--workflow-id",
        "W",
        "--profile",
        profile.ref_id,
        "--inputs",
        inputs.ref_id,
    )
    code, _ = cli_e3(
        tmp_path,
        "confirm",
        "--workflow-id",
        "W",
        "--preflight",
        view["confirm_with"],
        fault="life:after_complete",
    )
    assert code == 53
    (run_id,) = [p.name for p in (ws.root / "archive").iterdir()]
    report = ws.root / "archive" / run_id / "reports" / "arm.main.json"
    data = json.loads(report.read_text(encoding="utf-8"))
    data["report_fingerprint"] = "0" * 64  # 有人改了 E1 已经写好的城市报告
    report.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    before = tree(ws.root / "archive")
    _, out = cli_e3(tmp_path, "resume", "--workflow-id", "W")
    assert out["stage"] == "needs_attention" and out["attention"]["code"] == "archive_conflict"
    assert out["result"] is None and tree(ws.root / "archive") == before
    assert not (ws.root / "workflow/results").exists()


def test_changed_compiled_config_is_refused_before_any_execution(tmp_path):
    ws, rt, run_id = _halted(tmp_path)
    run_yaml = next((ws.root / "compiled").glob("*/run.yaml"))
    run_yaml.write_text(
        run_yaml.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8", newline="\n"
    )
    before = tree(ws.root / "archive")
    with rt:
        out = rt.resolve("W", "retry")
    assert out["attention"]["code"] == "archive_conflict"
    assert tree(ws.root / "archive") == before and _progress(ws, run_id)["status"] == "halted"


def test_run_index_mapping_conflict_is_refused(tmp_path):
    ws, rt, run_id = _halted(tmp_path)
    index = ws.root / "workflow/runs" / f"{run_id}.json"
    data = json.loads(index.read_text(encoding="utf-8"))
    data["run_id"] = "run-e3a-someone-else"
    index.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    before = tree(ws.root / "archive")
    with rt:
        out = rt.resolve("W", "retry")
    assert out["attention"]["code"] == "run_binding_conflict"
    assert tree(ws.root / "archive") == before


def test_partially_initialized_run_is_not_guessed_or_rebuilt(tmp_path):
    ws = E3Workspace.prepare(tmp_path / "ws")
    profile, inputs = seed_example(ws, "normal")
    _, view = cli_e3(
        tmp_path,
        "start",
        "--workflow-id",
        "W",
        "--profile",
        profile.ref_id,
        "--inputs",
        inputs.ref_id,
    )
    cli_e3(
        tmp_path,
        "confirm",
        "--workflow-id",
        "W",
        "--preflight",
        view["confirm_with"],
        fault="submit:after_register",
    )
    (entry,) = json.loads(
        (ws.root / "registry/submissions.json").read_text(encoding="utf-8")
    ).values()
    half = ws.root / "archive" / entry["run_id"]
    half.mkdir(parents=True)
    (half / "manifest.json").write_text("{}", encoding="utf-8")  # 只建了目录与运行清单，进度还没写
    _, out = cli_e3(tmp_path, "resume", "--workflow-id", "W")
    assert (
        out["stage"] == "needs_attention"
        and out["attention"]["code"] == "run_partially_initialized"
    )
    assert sorted(p.name for p in half.iterdir()) == ["manifest.json"]


# ---------- 11. 投影：出局、模拟问题、暂停照实；内部异常不泄漏 ----------


def test_mixed_and_paused_runs_are_projected_as_e3_reports_them(tmp_path):
    _, mixed = via_workflow(tmp_path / "mixed", "mixed")
    cities = {c["city_ref"]: c for c in mixed["result"]["cities"]}
    k51, m52 = cities["US:SYNTH2026:K51"], cities["US:SYNTH2026:M52"]
    assert (k51["planned"], k51["complete"], k51["exits"], k51["missing"]) == (2, 1, 1, 0)
    assert (m52["planned"], m52["complete"], m52["exits"], m52["missing"]) == (2, 0, 1, 1)
    assert m52["mean_j"] is None  # C = 0：没有均值，不补 0
    _, paused = via_workflow(tmp_path / "paused", "paused")
    assert paused["stage"] == "delivered" and paused["result"]["run_stage"] == "paused"
    reasons = {
        c["clone_ref"]: (c["category"], c["missing_reason"]) for c in paused["result"]["clones"]
    }
    assert reasons == {
        "u606.k51.r0": ("M", "input_config_program_error"),
        "u606.m52.r0": ("M", "not_run"),
    }
    text = json.dumps(paused, ensure_ascii=False)
    assert not any(s in text for s in SENTINELS)


def test_internal_errors_in_the_e3_adapter_never_reach_the_public_view(tmp_path, monkeypatch):
    _ws, rt, view = _start(tmp_path)

    def boom(self, run_id):
        raise RuntimeError("SENTINEL-INTERNAL C:/Users/someone/secret.txt")

    from workflows import e3_adapters

    monkeypatch.setattr(e3_adapters.E3ProjectionAdapter, "result", boom)
    with rt:
        out = rt.confirm("W", view["confirm_with"])
    text = json.dumps(out, ensure_ascii=False)
    assert "SENTINEL" not in text and "C:/Users" not in text and "RuntimeError" not in text
    assert out["error"]["code"] == "internal_error" and out["stage"] == "running"
    assert out["progress"]["stage"] == "completed"  # E1 已经跑完；外层没有把“读结果出错”写成交付


def test_a_cached_result_that_was_changed_is_refused(tmp_path):
    ws, view = via_workflow(tmp_path, "normal")
    cached = ws.root / "workflow/results" / f"{view['run_id']}.json"
    data = json.loads(cached.read_text(encoding="utf-8"))
    data["cities"][0]["complete"] = 0
    cached.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8", newline="\n")
    with WorkflowRuntime(tmp_path / "wf.db", e3_services(ws)) as rt:
        status = rt.status(f"wf-{'normal'}")
    assert status["result"] is None and status["error"]["code"] == "archive_conflict"


# ---------- 其他边界 ----------


def test_workspace_name_conflicts_and_stale_references_are_blocked_locally(tmp_path):
    ws = E3Workspace.prepare(tmp_path / "ws")
    p1, i1 = seed_example(ws, "normal")
    p2, i2 = seed_example(ws, "paused", name="other")
    raw = json.loads((ws.root / "workflow/requests/other.json").read_text(encoding="utf-8"))
    raw["label"] = "normal"  # 另一组条件想用同一个编译目录名
    ws.save_request("other", RunRequest.model_validate(raw))
    i2 = ws.request_ref("other")
    with WorkflowRuntime(tmp_path / "wf.db", e3_services(ws)) as rt:
        # 还没有登记运行时，同名不同条件可以先检查（提交之前改了输入是正常的）
        early = rt.start(workflow_id="E", profile=p2, inputs=i2)
        assert early["stage"] == "awaiting_confirmation"
        assert rt.decline("E")["stage"] == "declined"
        a = rt.start(workflow_id="A", profile=p1, inputs=i1)
        a = rt.confirm("A", a["confirm_with"])
        assert a["stage"] == "delivered"
        compiled = tree(ws.root / "compiled" / "normal")
        # A 的运行已经登记：别的条件想用同一个编译目录名，在预检查时就被挡下，A 的编译输入一个字节都没变
        b = rt.start(workflow_id="B", profile=p2, inputs=i2)
        assert b["stage"] == "blocked" and b["preflight"]["block"]["code"] == "workspace_conflict"
        assert b["preflight"]["public"]["workflow_local_check"] is True
        assert tree(ws.root / "compiled" / "normal") == compiled
        assert rt.status("A")["result"] == a["result"]
        stale = rt.start(
            workflow_id="C",
            profile=p1,
            inputs=i1.__class__(kind="run_inputs", ref_id="normal", fingerprint="0" * 64),
        )
        assert (
            stale["stage"] == "blocked" and stale["preflight"]["block"]["code"] == "reference_stale"
        )


def test_real_model_planners_are_not_wired_in_this_prototype(tmp_path):
    ws = E3Workspace.prepare(tmp_path / "ws")
    ctx = E3Context(ws)

    class _Llm:
        class config:  # noqa: N801 - 只模拟用到的属性
            class planner:  # noqa: N801
                class identity:  # noqa: N801
                    kind = "llm"

    from workflows.ports import AttentionRequiredError

    with pytest.raises(AttentionRequiredError) as e:
        ctx.planner(_Llm())
    assert e.value.code == "real_planner_not_supported"


def test_a_not_yet_wired_service_still_stops_cleanly(tmp_path):
    """接口占位（NotWiredError）的处理仍然保留：将来接新服务时，没接好的接口停在 not_wired，不登记任何东西。"""

    class _Pending:
        def check(self, profile, inputs):
            raise NotWiredError("占位")

        def describe(self, preflight):
            raise NotWiredError("占位")

    world = SyntheticWorld(tmp_path / "world")
    s = world.services()
    services = WorkflowServices(_Pending(), s.submission, s.life, s.projection, label="pending")
    profile = world.write_input("profile", {"p": 1}, kind="confirmed_profile")
    inputs = world.write_input(
        "normal",
        {"years": 1, "cities": ["K51"], "outcomes": {}, "max_paid_calls": 1},
        kind="run_inputs",
    )
    with WorkflowRuntime(tmp_path / "wf.db", services) as rt:
        view = rt.start(workflow_id="W", profile=profile, inputs=inputs)
    assert (
        view["error"]["code"] == "not_wired" and view["run_id"] is None and world.registry() == {}
    )
