"""外层流程（LangGraph 原型）的机制测试。服务全部是合成替身（``workflows.synthetic``），只检验流程本身：

确认之前不启动、确认绑定预检查身份、幂等提交、三种崩溃位置的恢复、需要处理时不自动重试、公开视图不泄漏。
真实 E3 / E1 接入之后的对照（直接路径与 LangGraph 路径逐项相同）不在这里，见 docs/design/langgraph-product-workflow.md 第 7 节。
"""

from __future__ import annotations

import json

import pytest

from workflows import runtime as runtime_module
from workflows.ports import RunInspection, WorkflowServices
from workflows.runtime import WorkflowRefusedError, WorkflowRuntime
from workflows.synthetic import LEAK_SENTINEL, SyntheticWorld

from .workflow_support import cli, runtime, seed, tree_hashes, years_executed


@pytest.fixture
def world(tmp_path):
    return SyntheticWorld(tmp_path / "world")


# ---------- 1. 完整故事：跨进程 ----------


def test_full_story_across_processes(tmp_path, world):
    seed(world)
    code, view = cli(
        tmp_path, "start", "--workflow-id", "W1", "--profile", "profile", "--inputs", "normal"
    )
    assert (
        code == 0
        and view["stage"] == "awaiting_confirmation"
        and view["waiting_for"] == "confirmation"
    )
    assert world.registry() == {} and world.run_dirs() == []  # 等待确认时什么都没登记

    code, again = cli(tmp_path, "status", "--workflow-id", "W1")  # 换一个进程读回
    assert (
        again["confirm_with"] == view["confirm_with"] and again["stage"] == "awaiting_confirmation"
    )

    code, done = cli(
        tmp_path, "confirm", "--workflow-id", "W1", "--preflight", view["confirm_with"]
    )
    assert code == 0 and done["stage"] == "delivered" and done["finished"]
    run_id = done["run_id"]
    assert years_executed(world, run_id) == [1, 2, 3]
    assert world.run_state(run_id)["planner_calls"] == 3
    assert done["result"]["counts"] == {"C": 1, "E": 0, "M": 1, "N": 2}

    code, later = cli(tmp_path, "status", "--workflow-id", "W1")
    assert later["result"] == done["result"] and later["run_id"] == run_id


# ---------- 2. 未确认、拒绝、过期确认都不启动 ----------


def test_nothing_runs_before_a_valid_confirmation(tmp_path, world):
    profile, inputs = seed(world)
    with runtime(tmp_path, world) as rt:
        view = rt.start(workflow_id="W", profile=profile, inputs=inputs)
        assert view["stage"] == "awaiting_confirmation"

        stale = rt.confirm("W", "0" * 64)  # 不是这份检查的身份
        assert (
            stale["stage"] == "awaiting_confirmation"
            and stale["notice"]["code"] == "confirmation_stale"
        )

        declined = rt.decline("W")
        assert declined["stage"] == "declined" and declined["finished"]
        assert rt.confirm("W", view["confirm_with"])["notice"]["code"] == "nothing_pending"
    assert world.registry() == {} and world.run_dirs() == []


def test_confirmation_expires_when_inputs_change_after_the_check(tmp_path, world):
    profile, inputs = seed(world)
    with runtime(tmp_path, world) as rt:
        view = rt.start(workflow_id="W", profile=profile, inputs=inputs)
        world.write_input(
            "normal",
            {"years": 3, "cities": ["K51"], "outcomes": {}, "max_paid_calls": 99},
            kind="run_inputs",
        )
        expired = rt.confirm(
            "W", view["confirm_with"]
        )  # 确认的是旧检查；登记前的再检查发现条件变了
        assert expired["stage"] == "blocked" and expired["notice"]["code"] == "inputs_changed"
        assert expired["preflight"]["block"]["code"] == "reference_stale"
        assert world.registry() == {} and world.run_dirs() == []

        revised = rt.revise("W", inputs=world.ref("normal", kind="run_inputs"))
        assert (
            revised["stage"] == "awaiting_confirmation"
            and revised["confirm_with"] != view["confirm_with"]
        )
        assert (
            rt.confirm("W", view["confirm_with"])["notice"]["code"] == "confirmation_stale"
        )  # 旧身份不能再用
        done = rt.confirm("W", revised["confirm_with"])
        assert done["stage"] == "delivered"
        assert done["result"]["cities"] == {"K51": {"category": "M"}}


def test_blocked_preflight_waits_for_revision_or_cancel(tmp_path, world):
    profile, inputs = seed(world, blocked=True)
    with runtime(tmp_path, world) as rt:
        view = rt.start(workflow_id="W", profile=profile, inputs=inputs)
        assert (
            view["stage"] == "blocked"
            and view["waiting_for"] == "revision"
            and view["confirm_with"] is None
        )
        assert (
            rt.confirm("W", view["preflight"]["fingerprint"])["notice"]["code"] == "nothing_pending"
        )
        assert rt.decline("W")["stage"] == "declined"
    assert world.run_dirs() == []


# ---------- 3. 幂等提交 ----------


def test_same_key_same_inputs_is_one_run_and_different_inputs_are_refused(tmp_path, world):
    profile, inputs = seed(world)
    other = world.write_input(
        "shorter",
        {
            **json.loads((world.root / "inputs" / "normal.json").read_text(encoding="utf-8")),
            "years": 2,
        },
        kind="run_inputs",
    )
    with runtime(tmp_path, world) as rt:
        a = rt.start(workflow_id="A", profile=profile, inputs=inputs, idempotency_key="click-1")
        a = rt.confirm("A", a["confirm_with"])
        b = rt.start(workflow_id="B", profile=profile, inputs=inputs, idempotency_key="click-1")
        b = rt.confirm("B", b["confirm_with"])
        assert a["run_id"] == b["run_id"] and b["stage"] == "delivered"
        assert years_executed(world, a["run_id"]) == [1, 2, 3]  # 第二个流程只读了已完成的运行

        c = rt.start(workflow_id="C", profile=profile, inputs=other, idempotency_key="click-1")
        c = rt.confirm("C", c["confirm_with"])
        assert c["stage"] == "needs_attention" and c["attention"]["code"] == "identity_conflict"
        assert c["run_id"] is None
    assert len(world.registry()) == 1 and world.run_dirs() == [a["run_id"]]


def test_restarting_a_workflow_id_is_idempotent_only_for_the_same_inputs(tmp_path, world):
    profile, inputs = seed(world)
    other = world.write_input(
        "other",
        {"years": 1, "cities": ["K51"], "outcomes": {}, "max_paid_calls": 3},
        kind="run_inputs",
    )
    with runtime(tmp_path, world) as rt:
        first = rt.start(workflow_id="W", profile=profile, inputs=inputs)
        assert rt.start(workflow_id="W", profile=profile, inputs=inputs) == first
        with pytest.raises(WorkflowRefusedError) as e:
            rt.start(workflow_id="W", profile=profile, inputs=other)
        assert e.value.code == "workflow_exists"


# ---------- 4–6. 三种崩溃位置（真的杀掉进程） ----------


def test_crash_after_the_submission_is_registered_does_not_create_a_second_run(tmp_path, world):
    seed(world)
    _, view = cli(
        tmp_path, "start", "--workflow-id", "W", "--profile", "profile", "--inputs", "normal"
    )
    code, out = cli(
        tmp_path,
        "confirm",
        "--workflow-id",
        "W",
        "--preflight",
        view["confirm_with"],
        fault="submit:after_register",
    )
    assert code == 41 and out is None
    assert len(world.registry()) == 1  # 登记已写下，checkpoint 还停在登记之前
    (run_id,) = world.run_dirs()
    assert years_executed(world, run_id) == []

    code, mid = cli(tmp_path, "status", "--workflow-id", "W")
    assert mid["stage"] == "submitting" and mid["run_id"] is None

    code, done = cli(tmp_path, "resume", "--workflow-id", "W")
    assert done["stage"] == "delivered" and done["run_id"] == run_id
    assert len(world.registry()) == 1 and world.run_dirs() == [run_id]
    assert years_executed(world, run_id) == [1, 2, 3]


def test_crash_inside_a_year_resumes_with_the_saved_reply(tmp_path, world):
    seed(world)
    _, view = cli(
        tmp_path, "start", "--workflow-id", "W", "--profile", "profile", "--inputs", "normal"
    )
    code, _ = cli(
        tmp_path,
        "confirm",
        "--workflow-id",
        "W",
        "--preflight",
        view["confirm_with"],
        fault="life:mid_year:2",
    )
    assert code == 42
    (run_id,) = world.run_dirs()
    assert years_executed(world, run_id) == [1] and world.run_state(run_id)["planner_calls"] == 2

    code, done = cli(tmp_path, "resume", "--workflow-id", "W")
    assert done["stage"] == "delivered"
    assert years_executed(world, run_id) == [1, 2, 3]
    assert world.run_state(run_id)["planner_calls"] == 3  # 第 2 年的回复已保存，没有再“调用”一次


def test_life_completed_before_the_outer_checkpoint_is_only_read_back(tmp_path, world):
    seed(world)
    _, view = cli(
        tmp_path, "start", "--workflow-id", "W", "--profile", "profile", "--inputs", "normal"
    )
    code, _ = cli(
        tmp_path,
        "confirm",
        "--workflow-id",
        "W",
        "--preflight",
        view["confirm_with"],
        fault="life:after_complete",
    )
    assert code == 43
    (run_id,) = world.run_dirs()
    assert world.run_state(run_id)["status"] == "completed"
    _, mid = cli(tmp_path, "status", "--workflow-id", "W")
    assert mid["stage"] == "running"  # 外层还没记下完成

    before = tree_hashes(world.root / "runs")
    code, done = cli(tmp_path, "resume", "--workflow-id", "W")
    assert done["stage"] == "delivered"
    assert tree_hashes(world.root / "runs") == before  # 只读核对，没有重新模拟
    assert years_executed(world, run_id) == [1, 2, 3]


# ---------- 5. 支持的边界：服务失败暂停后由人发起恢复 ----------


def test_service_halt_waits_for_a_person_and_resumes_through_e1(tmp_path, world):
    profile, inputs = seed(world, halt={"after_year": 1, "kind": "service_unavailable"})
    with runtime(tmp_path, world) as rt:
        view = rt.start(workflow_id="W", profile=profile, inputs=inputs)
        stopped = rt.confirm("W", view["confirm_with"])
        assert (
            stopped["stage"] == "needs_attention"
            and stopped["attention"]["code"] == "service_unavailable"
        )
        run_id = stopped["run_id"]
        assert (
            years_executed(world, run_id) == [1] and world.run_state(run_id)["planner_calls"] == 1
        )
        assert rt.resume("W")["stage"] == "needs_attention"  # 恢复不会自己重试

        done = rt.resolve("W", "retry")
        assert done["stage"] == "delivered"
        assert (
            years_executed(world, run_id) == [1, 2, 3]
            and world.run_state(run_id)["planner_calls"] == 3
        )


# ---------- 7. 重复操作不重复执行 ----------


def test_repeated_operations_after_delivery_change_nothing(tmp_path, world):
    profile, inputs = seed(world)
    with runtime(tmp_path, world) as rt:
        view = rt.start(workflow_id="W", profile=profile, inputs=inputs)
        done = rt.confirm("W", view["confirm_with"])
        before = tree_hashes(world.root)
        assert rt.confirm("W", view["confirm_with"])["notice"]["code"] == "already_confirmed"
        assert rt.resume("W")["result"] == done["result"]
        assert rt.resume("W")["result"] == done["result"]
        assert rt.resolve("W", "retry")["notice"]["code"] == "nothing_pending"
        assert rt.status("W")["result"] == done["result"]
        assert tree_hashes(world.root) == before


def test_a_result_changed_after_delivery_is_reported_not_served(tmp_path, world):
    profile, inputs = seed(world)
    with runtime(tmp_path, world) as rt:
        view = rt.start(workflow_id="W", profile=profile, inputs=inputs)
        run_id = rt.confirm("W", view["confirm_with"])["run_id"]
        result = world.root / "runs" / run_id / "result.json"
        result.write_text(
            result.read_text(encoding="utf-8").replace('"M"', '"C"'), encoding="utf-8", newline="\n"
        )
        status = rt.status("W")
    assert status["result"] is None and status["error"]["code"] == "archive_conflict"


# ---------- 8. 归档冲突、映射冲突、版本不符：执行前拒绝，原始记录不改 ----------


def test_tampered_archive_is_refused_without_rewriting_it(tmp_path, world):
    profile, inputs = seed(world, halt={"after_year": 1, "kind": "service_unavailable"})
    with runtime(tmp_path, world) as rt:
        view = rt.start(workflow_id="W", profile=profile, inputs=inputs)
        run_id = rt.confirm("W", view["confirm_with"])["run_id"]
        run_json = world.root / "runs" / run_id / "run.json"
        run_json.write_text(
            run_json.read_text(encoding="utf-8").replace(
                '"planner_calls": 1', '"planner_calls": 0'
            ),
            encoding="utf-8",
            newline="\n",
        )
        before = tree_hashes(world.root / "runs")
        again = rt.resolve("W", "retry")
        assert (
            again["stage"] == "needs_attention" and again["attention"]["code"] == "archive_conflict"
        )
        assert tree_hashes(world.root / "runs") == before
        assert years_executed(world, run_id) == [1]


class _SwappedLife:
    """E1 适配层返回了别的运行编号（映射冲突）。"""

    def __init__(self, inner):
        self.inner = inner

    def inspect(self, run_id):
        seen = self.inner.inspect(run_id)
        return RunInspection(
            run_id="syn-someone-else", kind=seen.kind, archive=seen.archive, code=seen.code
        )

    def start(self, run_id):  # pragma: no cover - 不应当被调用
        raise AssertionError("映射冲突时不能启动")

    resume = start


def test_run_id_mapping_conflict_stops_before_any_execution(tmp_path, world):
    profile, inputs = seed(world)
    s = world.services()
    services = WorkflowServices(
        s.preflight, s.submission, _SwappedLife(s.life), s.projection, label="synthetic-swapped"
    )
    with WorkflowRuntime(tmp_path / "wf.db", services) as rt:
        view = rt.start(workflow_id="W", profile=profile, inputs=inputs)
        out = rt.confirm("W", view["confirm_with"])
        assert (
            out["stage"] == "needs_attention" and out["attention"]["code"] == "run_binding_conflict"
        )
        assert years_executed(world, out["run_id"]) == []


def test_flow_version_mismatch_is_refused_and_the_database_is_untouched(
    tmp_path, world, monkeypatch
):
    profile, inputs = seed(world)
    with runtime(tmp_path, world) as rt:
        rt.start(workflow_id="W", profile=profile, inputs=inputs)
    before = (tmp_path / "wf.db").read_bytes()
    monkeypatch.setattr(runtime_module, "FLOW_VERSION", "lg-product-flow-v999")
    with runtime(tmp_path, world) as rt:
        for op in (
            lambda: rt.status("W"),
            lambda: rt.resume("W"),
            lambda: rt.confirm("W", "0" * 64),
        ):
            with pytest.raises(WorkflowRefusedError) as e:
                op()
            assert e.value.code == "flow_version_mismatch"
    assert (tmp_path / "wf.db").read_bytes() == before


# ---------- 9. 发送不确定与预算阻断不会被外层重试绕过 ----------


@pytest.mark.parametrize(
    ("overrides", "code", "years", "calls"),
    [
        ({"halt": {"after_year": 1, "kind": "uncertain_send"}}, "uncertain_send", [1], 1),
        ({"halt": {"after_year": 1, "kind": "model_mismatch"}}, "model_mismatch", [1], 1),
        ({"max_paid_calls": 2}, "budget_blocked", [1, 2], 2),
    ],
)
def test_e1_stop_rules_survive_outer_retries(tmp_path, world, overrides, code, years, calls):
    profile, inputs = seed(world, **overrides)
    with runtime(tmp_path, world) as rt:
        view = rt.start(workflow_id="W", profile=profile, inputs=inputs)
        out = rt.confirm("W", view["confirm_with"])
        run_id = out["run_id"]
        for _ in range(3):
            assert out["stage"] == "needs_attention" and out["attention"]["code"] == code
            assert (
                years_executed(world, run_id) == years
                and world.run_state(run_id)["planner_calls"] == calls
            )
            out = rt.resolve("W", "retry")
        assert rt.resolve("W", "abandon")["stage"] == "abandoned"
        assert world.run_state(run_id)["planner_calls"] == calls  # 放弃不碰 E1 的归档


def test_no_node_has_an_automatic_retry_policy(tmp_path, world):
    with runtime(tmp_path, world) as rt:
        nodes = rt.graph.builder.nodes
        assert set(nodes) == {
            "preflight",
            "await_confirmation",
            "blocked",
            "revalidate",
            "submit",
            "run_life",
            "deliver",
            "await_resolution",
        }
        for name, spec in nodes.items():
            assert not spec.retry_policy, name


# ---------- 11. 公开视图：E / M 照实、未生成不补零、内部异常不泄漏 ----------


def test_internal_errors_never_reach_the_public_view(tmp_path):
    world = SyntheticWorld(tmp_path / "world", faults={"projection:raise"})
    profile, inputs = seed(world)
    with runtime(tmp_path, world) as rt:
        view = rt.start(workflow_id="W", profile=profile, inputs=inputs)
        out = rt.confirm("W", view["confirm_with"])
        status = rt.status("W")
    for v in (out, status):
        text = json.dumps(v, ensure_ascii=False)
        assert (
            "SENTINEL" not in text
            and "C:/Users" not in text
            and "Traceback" not in text
            and "RuntimeError" not in text
        )
        assert v["error"]["code"] == "internal_error" and v["error"]["audit_ref"].startswith(
            "wf-diag-"
        )
        assert v["result"] is None  # 结果没生成就是空，不补零
    assert out["stage"] == "running"  # 外层没有把“读结果出错”写成交付
    diag = (tmp_path / "diagnostics" / f"{out['error']['audit_ref']}.json").read_text(
        encoding="utf-8"
    )
    assert LEAK_SENTINEL in diag


def test_exits_and_missing_clones_are_projected_as_given(tmp_path, world):
    profile, inputs = seed(
        world, cities=["K51", "M52", "N73"], outcomes={"K51": "C", "M52": "E", "N73": "M"}
    )
    with runtime(tmp_path, world) as rt:
        view = rt.start(workflow_id="W", profile=profile, inputs=inputs)
        assert view["result"] is None and view["progress"] is None
        done = rt.confirm("W", view["confirm_with"])
    assert done["result"]["counts"] == {"C": 1, "E": 1, "M": 1, "N": 3}
    assert done["result"]["cities"]["M52"] == {"category": "E"} and done["result"]["cities"][
        "N73"
    ] == {"category": "M"}
