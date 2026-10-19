"""E1-7B 收尾（独立审核 F1–F3，DECISIONS/0031 第八节）的永久测试。全部离线替身、临时配置与临时账本，零网络、零费用。

F1 服务回复：首次、缓存、恢复与重放共用一份一致性检查；返回模型不同的回复永远不能用来规划，但服务与费用事实保留；合法的已保存回复
仍能零新增请求继续。F2 预算：实际持有的账本必须就是声明的那一本；恢复之前本运行的费用历史必须都在账上；共享账本里的其他运行照常
存在、额度按整本账算。F3 税说明：按实际第一档税率写。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from agentlayer.life_llm import LlmPlanner, ProviderCallLog, service_history
from agentlayer.life_planning import visible_tax_rules
from common.money import Money
from econ.life_settlement import SyntheticBracketTax
from orchestration.life_budget import RoundBudget, read_ledger
from orchestration.life_runner import (
    RunInterrupted,
    RunRefusedError,
    budget_reconciliation,
    replay_run,
    resume_run,
    start_run,
)

from .live_support import (
    MODEL,
    ScriptedTransport,
    has_records,
    live_world,
    make_repo,
    mock_text,
    payload_of,
    qwen,
    records,
    reply,
    rewrite_records,
    snapshot,
)

FIRST = "u301.k51.r0"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = make_repo(tmp_path / "repo")
    monkeypatch.chdir(root)
    return root


def _ledger(world) -> RoundBudget:
    return RoundBudget(world.config.paid_budget)


def _rewrite(ledger: RoundBudget, fn) -> None:
    rows = ledger.events()
    fn(rows)
    ledger.replace_events(rows)


class _CrashAfterReply(LlmPlanner):
    """服务回复已经写进明细，规划记录还没写：进程在这里中断。"""

    def plan(self, call):
        super().plan(call)
        raise RunInterrupted("回复落盘之后、规划记录之前中断")


def _pending(world, out: Path) -> None:
    p = _CrashAfterReply(
        world.config.planner.llm, transport=ScriptedTransport(), budget=_ledger(world)
    )
    assert start_run(world, p, out, run_id="c7b").status == "interrupted"


# ---------------------------------------------------------------------- F1：返回模型不同


def test_a_wrong_model_reply_stays_rejected_after_restart(repo, tmp_path):
    world = live_world()
    wrong = lambda body: reply(mock_text(body), body, model="other-model-2026-01-01")  # noqa: E731
    out = tmp_path / "run"
    first = start_run(world, qwen(world, ScriptedTransport([wrong])), out, run_id="c7b")
    assert (first.status, first.halt_kind) == ("halted", "model_mismatch")
    recs = ProviderCallLog(out).records()
    outcome = recs[1]
    # 服务成功返回、可能已经产生费用：原文、模型名、用量与费用都保留；只是不能用来规划
    assert (outcome.status, outcome.returned_model, outcome.usable_for_planning) == (
        "ok",
        "other-model-2026-01-01",
        False,
    )
    assert outcome.response["text"] and outcome.cost_status == "estimated_from_usage"
    assert _ledger(world).totals().requests == 1 and _ledger(world).totals().settled_cny > 0
    for _ in range(2):  # 重新开机两次：照样停下，不重试、不换模型、不新增年度
        t = ScriptedTransport()
        again = resume_run(world, qwen(world, t), out, stop_after_commits=1)
        assert (again.status, again.halt_kind, t.bodies) == ("halted", "model_mismatch", [])
        assert not has_records(out, "accepted") and not has_records(out, "calls")
    assert _ledger(world).totals().requests == 1


# ---------------------------------------------------------------------- F1：服务明细被改


@pytest.mark.parametrize(
    "edit", ["body", "settings", "owner", "temperature", "missing_intent", "duplicate_outcome"]
)
def test_b_a_tampered_pending_service_record_is_refused_before_any_write(repo, tmp_path, edit):
    world = live_world()
    out = tmp_path / "run"
    _pending(world, out)

    def change(rows):
        if edit == "body":
            rows[0]["request"]["messages"][1]["content"] = "这份请求属于另一次模拟。"
        elif edit == "settings":
            rows[0]["settings_fingerprint"] = "0" * 64
        elif edit == "owner":
            rows[1]["clone_id"] = "u301.m52.r0"
        elif edit == "temperature":
            rows[0]["request"]["temperature"] = 1.9
        elif edit == "missing_intent":
            rows.pop(0)
        else:
            rows.append(dict(rows[1]))

    rewrite_records(out, "provider_calls", change)
    before = snapshot(out)
    t = ScriptedTransport()
    with pytest.raises(RunRefusedError):
        resume_run(world, qwen(world, t), out, stop_after_commits=1)
    assert snapshot(out) == before and t.bodies == []


def test_b_a_changed_request_after_a_committed_year_is_refused_by_resume_and_replay(repo, tmp_path):
    world = live_world()
    out = tmp_path / "run"
    start_run(world, qwen(world, ScriptedTransport()), out, run_id="c7b", stop_after_commits=1)
    rewrite_records(out, "provider_calls", lambda rows: rows[0]["request"].update(max_tokens=8192))
    before = snapshot(out)
    with pytest.raises(RunRefusedError, match="请求正文"):
        replay_run(world, out)
    t = ScriptedTransport()
    with pytest.raises(RunRefusedError):
        resume_run(world, qwen(world, t), out)
    assert snapshot(out) == before and t.bodies == []


def test_b_the_cache_compares_the_rebuilt_request_not_only_the_record(repo, tmp_path):
    """归档自身一致（载荷、正文、设定与账本互相对得上）但不是这次运行会发出的那份请求：只有缓存命中时与重建的请求逐字节比较能发现，
    拒绝，并且在写任何东西之前（运行器把服务明细对不上变成拒绝，不当成规划器异常写进记录）。"""
    from agentlayer.life_llm import estimate_input_tokens, expected_provider_body

    world = live_world()
    out = tmp_path / "run"
    _pending(world, out)
    ledger = _ledger(world)
    forged: dict = {}

    def swap(rows):
        payload = payload_of(rows[0]["request"])
        payload["observation"]["cash"]["amount"] = (
            999999.0  # 改了载荷，并按同一规则重新渲染出自洽的正文
        )
        new = expected_provider_body(world.config.planner.llm, payload)
        est = estimate_input_tokens(new["messages"])
        forged.update(
            est=est, reserved=str(ledger.cost(est, world.config.planner.llm.max_output_tokens))
        )
        rows[0].update(request=new, input_tokens_estimate=est, reserved_cny=forged["reserved"])

    rewrite_records(out, "provider_calls", swap)

    def fix_ledger(rows):  # 让账本与伪造的发送意图也一致：只剩"与重建的请求比较"这一层能发现
        for r in rows:
            if r["event"] == "reserve":
                r.update(input_tokens_estimate=forged["est"], reserved_cny=forged["reserved"])

    _rewrite(ledger, fix_ledger)
    before = snapshot(out)
    t = ScriptedTransport()
    with pytest.raises(RunRefusedError, match="对不上"):
        resume_run(world, qwen(world, t), out, stop_after_commits=1)
    assert snapshot(out) == before and t.bodies == []


@pytest.mark.parametrize("edit", ["owner", "settings"])
def test_b_replay_alone_refuses_a_tampered_pending_service_record(repo, tmp_path, edit):
    """重放不调用规划器：还没有规划记录的那一份服务历史，只能由读归档时的核对发现。"""
    world = live_world()
    out = tmp_path / "run"
    _pending(world, out)

    def change(rows):
        if edit == "owner":
            rows[1]["clone_id"] = "u301.m52.r0"
        else:
            rows[0]["settings_fingerprint"] = "0" * 64

    rewrite_records(out, "provider_calls", change)
    before = snapshot(out)
    with pytest.raises(RunRefusedError, match="还没有规划记录"):
        replay_run(world, out)
    assert snapshot(out) == before


# ---------------------------------------------------------------------- F1：合法中断


def test_c_a_saved_reply_continues_with_zero_requests_and_runs_once(repo, tmp_path):
    world = live_world()
    out = tmp_path / "run"
    _pending(world, out)
    t = ScriptedTransport()
    result = resume_run(world, qwen(world, t), out, stop_after_commits=1)
    assert result.status == "interrupted" and t.bodies == []
    assert len(records(out, "accepted")) == 1 and len(records(out, "calls")) == 1
    assert _ledger(world).totals().requests == 1
    replayed = replay_run(world, out)
    assert (replayed["planner_calls"], replayed["bindings_identical"]) == (0, True)


def _crash_on(world, out: Path, event: str, monkeypatch) -> None:
    """写某一种服务记录时进程中断（模拟持久化顺序上的中断点）。"""
    real = ProviderCallLog.append

    def append(self, record):
        if record.event == event:
            raise RunInterrupted(f"写 {event} 时中断")
        real(self, record)

    monkeypatch.setattr(ProviderCallLog, "append", append)
    try:
        assert (
            start_run(world, qwen(world, ScriptedTransport()), out, run_id="c7b").status
            == "interrupted"
        )
    finally:
        monkeypatch.setattr(ProviderCallLog, "append", real)


def test_d_reserved_but_intent_not_durable_reuses_the_reservation(repo, tmp_path, monkeypatch):
    """已预留、发送意图还没落盘：确定没有发出。恢复沿用那一份预留，只发一次，不重复计数。"""
    world = live_world()
    out = tmp_path / "run"
    _crash_on(world, out, "intent", monkeypatch)
    assert ProviderCallLog(out).records() == []
    t0 = _ledger(world).totals()
    assert (t0.requests, t0.outstanding_cny > 0) == (1, True)
    t = ScriptedTransport()
    resume_run(world, qwen(world, t), out, stop_after_commits=1)
    t1 = _ledger(world).totals()
    assert len(t.bodies) == 1 and t1.requests == 1 and t1.outstanding_cny == 0
    assert len(records(out, "accepted")) == 1


def test_d_settled_but_reply_lost_waits_for_the_owner(repo, tmp_path, monkeypatch):
    """费用已核算、回复没落盘：发出过、结果不明。停下等人，费用保留，不重发。"""
    world = live_world()
    out = tmp_path / "run"
    _crash_on(world, out, "outcome", monkeypatch)
    before = _ledger(world).totals()
    assert (before.requests, before.settled_cny > 0) == (1, True)
    t = ScriptedTransport()
    again = resume_run(world, qwen(world, t), out)
    assert (again.status, again.halt_kind, t.bodies) == ("halted", "uncertain_send", [])
    assert _ledger(world).totals() == before


def test_d_sent_but_no_reply_keeps_the_reservation(repo, tmp_path):
    class _CrashWhileSending:
        def __init__(self):
            self.bodies = []

        def send(self, body, *, timeout):
            self.bodies.append(body)
            raise RunInterrupted("请求可能已经发出")

    world = live_world()
    out = tmp_path / "run"
    assert (
        start_run(world, qwen(world, _CrashWhileSending()), out, run_id="c7b").status
        == "interrupted"
    )
    before = _ledger(world).totals()
    t = ScriptedTransport()
    again = resume_run(world, qwen(world, t), out)
    assert (again.status, again.halt_kind, t.bodies) == ("halted", "uncertain_send", [])
    assert _ledger(world).totals() == before and before.outstanding_cny > 0


# ---------------------------------------------------------------------- F2：预算身份


@pytest.mark.parametrize("field", ["max_requests", "max_cost_cny", "price", "ledger_dir"])
def test_e_the_planner_must_hold_the_declared_budget(repo, tmp_path, field):
    world = live_world()
    declared = world.config.paid_budget
    update = {
        "max_requests": {"max_requests": 149},
        "max_cost_cny": {"max_cost_cny": 49},
        "price": {"price": declared.price.model_copy(update={"input_per_million": 1})},
        "ledger_dir": {"ledger_dir": "other-budget"},
    }[field]
    if field != "ledger_dir":
        update["ledger_dir"] = (
            f"other-{field}"  # 不同额度的账要放在另一个目录（同一目录的头文件不允许改）
        )
    other = RoundBudget(declared.model_copy(update=update))
    t = ScriptedTransport()
    with pytest.raises(RunRefusedError, match=r"预算|账本"):
        start_run(world, qwen(world, t, budget=other), tmp_path / "run", run_id="c7b")
    assert t.bodies == [] and not (tmp_path / "run").exists()


def test_e_resume_also_checks_the_budget_identity(repo, tmp_path):
    world = live_world()
    out = tmp_path / "run"
    start_run(world, qwen(world, ScriptedTransport()), out, run_id="c7b", stop_after_commits=1)
    before = snapshot(out)
    other = RoundBudget(world.config.paid_budget.model_copy(update={"ledger_dir": "fresh-budget"}))
    t = ScriptedTransport()
    with pytest.raises(RunRefusedError, match="另一本账"):
        resume_run(world, qwen(world, t, budget=other), out)
    assert snapshot(out) == before and t.bodies == []


# ---------------------------------------------------------------------- F2：费用历史


@pytest.mark.parametrize("damage", ["emptied", "settle_removed", "cost_changed"])
def test_f_lost_or_changed_cost_history_is_refused_before_resume(repo, tmp_path, damage):
    world = live_world()
    out = tmp_path / "run"
    start_run(world, qwen(world, ScriptedTransport()), out, run_id="c7b", stop_after_commits=1)
    ledger = _ledger(world)
    rows = ledger.events()
    if damage == "emptied":
        rows = []
    elif damage == "settle_removed":
        rows = [r for r in rows if r["event"] != "settle"]
    else:
        next(r for r in rows if r["event"] == "settle")["cost_cny"] = "0.000001"
    ledger.replace_events(rows)
    before = snapshot(out)
    t = ScriptedTransport()
    with pytest.raises(RunRefusedError, match="账本"):
        resume_run(world, qwen(world, t), out)
    assert snapshot(out) == before and t.bodies == []
    report = budget_reconciliation(world, out)
    assert report["consistent"] is False and report["problems"]


def test_g_a_shared_ledger_counts_every_run_and_accepts_other_runs(tmp_path, monkeypatch):
    def two(r):
        r["paid_budget"]["max_requests"] = 2

    monkeypatch.chdir(make_repo(tmp_path / "repo", run=two))
    world = live_world()
    a = tmp_path / "run-a"
    start_run(world, qwen(world, ScriptedTransport()), a, run_id="run-a", stop_after_commits=1)
    # 另一次合法运行用同一本账：它的条目不妨碍本运行的核对，额度按整本账算
    b = tmp_path / "run-b"
    tb = ScriptedTransport(["散文"])  # 第一份候选不合格，第二份需要第 3 次请求 → 整本账只剩 0 次
    outcome = start_run(world, qwen(world, tb), b, run_id="run-b")
    assert (outcome.status, outcome.halt_kind, len(tb.bodies)) == ("halted", "budget", 1)
    assert _ledger(world).totals().requests == 2
    assert budget_reconciliation(world, a)["consistent"] is True
    assert budget_reconciliation(world, b)["consistent"] is True
    ta = (
        ScriptedTransport()
    )  # 先前那次运行照样可以恢复核对（共享账本里有 run-b 的条目也不误拒），只是没有额度了
    resumed = resume_run(world, qwen(world, ta), a, stop_after_commits=1)
    assert (resumed.status, resumed.halt_kind, ta.bodies) == ("halted", "budget", [])


def test_g_reconciliation_is_read_only(repo, tmp_path):
    world = live_world()
    out = tmp_path / "run"
    start_run(world, qwen(world, ScriptedTransport()), out, run_id="c7b", stop_after_commits=1)
    ledger_dir = Path(world.config.paid_budget.ledger_dir)
    before = {p.name: p.read_bytes() for p in ledger_dir.iterdir()}
    report = budget_reconciliation(world, out)
    assert (
        report["consistent"] is True
        and report["service_intents"] == report["ledger_reservations_for_run"] == 1
    )
    assert {p.name: p.read_bytes() for p in ledger_dir.iterdir()} == before
    for p in ledger_dir.iterdir():
        p.unlink()
    ledger_dir.rmdir()
    gone = budget_reconciliation(world, out)
    assert (
        gone["ledger_found"] is False
        and not ledger_dir.exists()
        and read_ledger(ledger_dir) is None
    )
    replayed = replay_run(world, out)  # 生活重放不需要账本，也不创建账本
    assert replayed["bindings_identical"] is True and not ledger_dir.exists()


def test_h_resume_does_not_reset_counts_or_double_book(repo, tmp_path):
    world = live_world()
    out = tmp_path / "run"
    start_run(
        world, qwen(world, ScriptedTransport(["散文"])), out, run_id="c7b", stop_after_calls=1
    )
    first = _ledger(world).totals()
    assert first.requests == 1
    t = ScriptedTransport()
    resume_run(world, qwen(world, t), out, stop_after_commits=1)
    after = _ledger(world).totals()
    attempts = [a for a in records(out, "attempts") if a["clone_id"] == FIRST]
    assert [a["attempt"] for a in attempts] == [1, 2]  # 第二份候选，不是从第一份重来
    assert after.requests == first.requests + len(t.bodies) == 2
    intents = [r for r in ProviderCallLog(out).records() if r.event == "intent"]
    assert len(intents) == after.requests


# ---------------------------------------------------------------------- F1 共用一份检查


def test_i_service_history_flags_each_kind_of_inconsistency(repo, tmp_path):
    world = live_world()
    out = tmp_path / "run"
    start_run(world, qwen(world, ScriptedTransport()), out, run_id="c7b", stop_after_commits=1)
    recs = ProviderCallLog(out).records()
    rid = recs[0].request_id
    kw = dict(
        request_id=rid,
        run_id="c7b",
        settings=world.config.planner.llm,
        price_version=world.config.paid_budget.price.price_version,
    )
    ok = service_history(recs, **kw)
    assert ok.problems == () and ok.usable is not None and ok.usable.outcome.returned_model == MODEL
    assert service_history(recs, **{**kw, "run_id": "other"}).problems
    other_settings = world.config.planner.llm.model_copy(update={"temperature": 0.3})
    assert service_history(recs, **{**kw, "settings": other_settings}).problems
    assert service_history([recs[1], recs[0]], **kw).problems  # 结果在意图之前
    assert service_history([recs[1]], **kw).problems  # 结果没有发送意图
    assert service_history(recs, **{**kw, "price_version": "other"}).problems


# ---------------------------------------------------------------------- F3 税说明


@pytest.mark.parametrize(("rate", "tax_1500"), [(0.0, 0), (0.1, 150)])
def test_j_the_tax_text_follows_the_first_bracket(tmp_path, monkeypatch, rate, tax_1500):
    def change(doc):
        doc["tax_policies"][0]["brackets"][0]["rate"] = rate

    monkeypatch.chdir(make_repo(tmp_path / "repo", life=change))
    world = live_world()
    policy = world.bundle.tax_policy("tax.synth.e1conv")
    engine = SyntheticBracketTax(policy).tax(
        Money(1500, policy.money_basis_id), policy.applies_to, 1
    )
    assert engine.amount == tax_1500  # 环境实际扣的税（第 1 月工资 1,500）
    rules = visible_tax_rules(policy).rules
    text = "".join(rules)
    assert "只对超出部分用更高税率" in rules[1]  # 累进：不是对全部收入套用最高一档
    if rate == 0:
        assert "累计收入还在第一档时不扣税" in text
    else:
        assert (
            "第一档时不扣税" not in text
            and "第一档税率是 10%" in text
            and "从第一笔工资起就按它预扣" in text
        )
    # 运行器发送前核对：请求里的税说明就是执行用的绑定的投影
    t = ScriptedTransport()
    start_run(world, qwen(world, t), tmp_path / "run", run_id="c7b", stop_after_commits=1)
    assert payload_of(t.bodies[0])["tax"]["rules"] == list(rules)
    assert Decimal(str(payload_of(t.bodies[0])["tax"]["brackets"][0]["rate"])) == Decimal(str(rate))
