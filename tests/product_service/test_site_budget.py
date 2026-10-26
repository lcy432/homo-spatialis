"""测试站总额控制（scripts/site_budget.py）：账本占用按实际 / 保留 / 预留算；最坏情况 = 已花 + 没撤销身份的未用次数 × 每次上限；
加一个身份会超过总额就拒绝。"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "site_budget.py"


@pytest.fixture(scope="module")
def sb(monkeypatch_module):
    # 脚本先 import scripts/_bootstrap（把 src/ 放上路径）；测试里 src/ 已经在路径上，只需让 _bootstrap 找得到
    monkeypatch_module.syspath_prepend(str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("site_budget", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # 先进 sys.modules 再执行：@dataclass 解析延迟注解时要按模块名找到它
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop(spec.name, None)


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_committed_cost_counts_settled_uncertain_and_open(sb):
    events = [
        {"event": "reserve", "key": "a", "reserved_cny": "0.50"},
        {"event": "settle", "key": "a", "cost_cny": "0.12"},
        {"event": "reserve", "key": "b", "reserved_cny": "0.40"},
        {"event": "uncertain", "key": "b", "cost_cny": "0.40"},
        {"event": "reserve", "key": "c", "reserved_cny": "0.30"},
    ]
    assert sb.committed_cost(events) == Decimal("0.82")


def _pos(sb, spent, holders, cap="300"):
    return sb.Position(Decimal(spent), 1, holders, Decimal("20"), 3, Decimal(cap))


def test_five_fresh_holders_fill_300_and_a_sixth_is_refused(sb):
    holders = [sb.Holder(f"pr-{i}", f"t{i}", False, 3, 0, 0) for i in range(4)]
    p = _pos(sb, "0", holders)
    assert p.worst_case == Decimal("240")
    assert p.can_add()  # 第 5 个：240 + 60 = 300
    p5 = _pos(sb, "0", [*holders, sb.Holder("pr-4", "t4", False, 3, 0, 0)])
    assert p5.worst_case == Decimal("300")
    assert not p5.can_add()


def test_spent_money_and_used_runs_and_revocation(sb):
    # 一个身份用了 1 次花了 3.5 元：剩 2 次 × 20；撤销的身份不再算未用次数，但已花的照算
    holders = [
        sb.Holder("pr-a", "a", False, 3, 1, 0),
        sb.Holder("pr-b", "b", True, 3, 1, 0),
    ]
    p = _pos(sb, "7.00", holders)
    assert p.open_exposure == Decimal("40")
    assert p.worst_case == Decimal("47.00")
    assert p.room == Decimal("253.00")
    # 预留中的次数按整次再算（宁可多算）
    p2 = _pos(sb, "1.00", [sb.Holder("pr-c", "c", False, 3, 0, 1)])
    assert p2.open_exposure == Decimal("60")
