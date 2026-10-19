"""从**实际 CLI** 进入，检查最终产物。

这个文件存在的唯一理由，是 2026-09-28 代码审核查出的那个失败模式：

> 模块写好了、单元测试通过了，**而正式入口仍然没有用它**。

`scheme_a.py` 有测试、`window.py` 有测试，两者都绿，但 `run_p8.py → runner`
跑的还是旧协议、还在拿 `std(λ)` 冒充 `SE(W)`。任何只测模块的测试都抓不到这件事——
**必须从入口进，检查落盘的那份 JSON。**

所以这里不 import 被测函数，只调 `scripts/run_p8.py` 的主流程，然后读产物。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from eval.floors.scheme_a import (
    LEGACY_PROTOCOL,
    PROTOCOL,
    PROTOCOL_VERSION,
    ProtocolMismatchError,
    protocol_of,
    require_formal_protocol,
)

#: 研究线（M5）从命令行真跑一遍，准备就要约 90 秒：日常默认不跑，用 -m slow 显式打开。
pytestmark = pytest.mark.slow

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cli_artifact(tmp_path_factory) -> dict:
    """真的跑一次 CLI，返回落盘的产物。

    规模压到能在测试里跑完：60 人设 × 3 个选择集 × 12 城，B=2。
    **协议与接线与主规模是同一条路径**，这正是要测的东西。
    """
    cfg = yaml.safe_load((REPO / "configs" / "p8_pilot.yaml").read_text(encoding="utf-8"))
    cfg["design"]["n_personas"] = 60
    cfg["design"]["n_choice_sets"] = 3
    cfg["repeat_subset"]["n_personas"] = 20
    cfg["bootstrap"]["n_replicates"] = 2

    work = tmp_path_factory.mktemp("p8cli")
    cfg_path = work / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    out_dir = work / "out"

    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "run_p8.py"),
            "--config",
            str(cfg_path),
            "--out",
            str(out_dir),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert proc.returncode == 0, f"CLI 退出码 {proc.returncode}\n{proc.stderr[-2000:]}"
    return json.loads((out_dir / "p8_selfcheck.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# F1：入口跑的是哪个协议
# --------------------------------------------------------------------------


def test_the_cli_artifact_uses_the_formal_protocol(cli_artifact):
    """**旧代码上这条失败。** 它跑的是权重空间的均值/协方差共同插值。"""
    assert protocol_of(cli_artifact) == f"{PROTOCOL}@{PROTOCOL_VERSION}"
    require_formal_protocol(cli_artifact)  # 不抛就算过


def test_the_legacy_artifact_is_refused_by_the_formal_gate():
    """历史 pilot 可以读，**但拿不到正式结果身份**。"""
    legacy = REPO / "runs" / "2026-09-16-p8-synthetic-pilot" / "p8_selfcheck.json"
    if not legacy.exists():
        pytest.skip("历史 pilot 不在这个检出里")
    payload = json.loads(legacy.read_text(encoding="utf-8"))
    assert protocol_of(payload) == LEGACY_PROTOCOL
    with pytest.raises(ProtocolMismatchError):
        require_formal_protocol(payload)


def test_an_artifact_without_a_protocol_field_is_not_taken_for_the_new_one():
    """缺字段只可能写在协议字段存在之前，所以它**一定**是旧协议。

    默认成新协议，旧数字就悄悄获得了新身份——这正是本轮要防的那件事。
    """
    assert protocol_of({"lambda_path": {}}) == LEGACY_PROTOCOL
    assert protocol_of({}) == LEGACY_PROTOCOL
    with pytest.raises(ProtocolMismatchError):
        require_formal_protocol({"lambda_path": {}})


def test_there_is_no_switch_that_promotes_the_legacy_protocol():
    """ "旧协议须显式选" ≠ "正式入口拒绝旧协议"。

    历史复现可以跑旧协议，但**不能靠一个开关让它变成正式结果**。所以
    `require_formal_protocol` 不该有任何放行参数。
    """
    import inspect

    params = set(inspect.signature(require_formal_protocol).parameters)
    assert params == {"payload"}
    assert not any("allow" in p or "legacy" in p or "force" in p for p in params)


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("centre_is_e_softmax_not_softmax_of_mean", True),
        ("matched_arm_is_placeholder", True),
    ],
)
def test_the_artifact_states_the_scheme_a_conditions(cli_artifact, field, expected):
    assert cli_artifact["lambda_path"][field] is expected


def test_the_injected_covariance_comes_from_this_list(cli_artifact):
    """条件 2：注入端取本次名单 S 的 ALR 矩，ddof=0，**不是** P 的闭式解。"""
    src = cli_artifact["lambda_path"]["injected_covariance_source"]
    assert src["estimand"] == "S_realised_list"
    assert src["ddof"] == 0
    assert src["n"] == cli_artifact["design"]["n_personas"]


def test_one_centre_is_shared_by_the_whole_path(cli_artifact):
    """条件 3：整条路径共用 `geom` 那一个中心，本函数不另造。"""
    lp = cli_artifact["lambda_path"]
    centre = lp["shared_geometry_digest"]["centre"]
    assert centre == cli_artifact["geometry"]["shared"]["centre"]
    assert len(centre) == 7
    assert sum(centre) == pytest.approx(1.0, abs=1e-9)


def test_a_failed_t_point_withholds_its_lambda(cli_artifact):
    """条件 4：校验不过就不发 λ。

    这条在两种情形下都要成立，所以断言写成蕴含式而不是"必须全过"——
    小规模下噪声地板本来就可能触发。
    """
    for point in cli_artifact["lambda_path"]["points"]:
        if point["status"] != "ok":
            assert point["lambda"] is None
            assert "lambda_withheld_because" in point
        else:
            assert point["lambda"] is not None


def test_every_t_point_reports_its_independent_check(cli_artifact):
    for point in cli_artifact["lambda_path"]["points"]:
        assert point["n_draws_check"] > point["n_draws_solve"] // 8
        assert "mean_abs_error_holdout" in point
        assert "mc_se_holdout" in point


# --------------------------------------------------------------------------
# F2：窗口 SE 是不是真的联合算的
# --------------------------------------------------------------------------


def test_the_bootstrap_actually_uses_both_arms(cli_artifact):
    """**旧代码上这条失败。** 它收下 `shuffled_arm` 之后完全不读。"""
    b = cli_artifact["bootstrap"]
    assert b["both_arms_share_one_persona_draw"] is True
    assert b["geometry_rebuilt_per_replicate"] is True
    assert len(b["s_shuffled_values"]) == len(b["lambda_values"])
    assert len(b["s_shuffled_values"]) > 0


def test_the_window_se_is_not_the_floor_slope_se(cli_artifact):
    """**旧代码把 `std(λ)` 同时写进 `lambda_se` 与 `window_se`。**

    联合 SE 是 `std(s − λ)`，它含 `−2·Cov`。只有当打乱臂方差为零且协方差为零
    时两者才相等，而那两件事都不成立。
    """
    b = cli_artifact["bootstrap"]
    assert "lambda_se" not in b, "不再输出会被误读成窗口 SE 的那个量"
    assert b["se_basis"].startswith("per-replicate")
    if b["window_se"] is not None:
        import numpy as np

        lam = np.array(b["lambda_values"])
        sh = np.array(b["s_shuffled_values"])
        assert b["window_se"] == pytest.approx(float(np.std(sh - lam, ddof=1)))


def test_the_replicate_ids_line_up_across_arms(cli_artifact):
    """两个臂的第 j 个数必须来自同一次抽样，否则 `Cov` 估的是噪声。"""
    b = cli_artifact["bootstrap"]
    assert len(b["replicate_ids"]) == len(b["lambda_values"])
    assert b["replicate_ids"] == sorted(b["replicate_ids"])


def test_small_b_is_not_dressed_up_as_a_formal_interval(cli_artifact):
    b = cli_artifact["bootstrap"]
    assert b["is_engineering_smoke_not_formal_interval"] is True
    assert b["status"] == "pending_computation"
    assert cli_artifact["gate_two"]["status"] in {"pending_input", "pending_computation"}


def test_sigma_is_declared_as_a_fixed_condition(cli_artifact):
    """σ 是固定条件还是也传播误差，必须写出来，不能靠读代码猜。"""
    assert "not_propagated" in cli_artifact["bootstrap"]["sigma_treated_as"]
    assert cli_artifact["lambda_path"]["sigma_treated_as"] == "fixed_exogenous_along_t"


# --------------------------------------------------------------------------
# 入口与模块之间不该再有第二条路
# --------------------------------------------------------------------------


def test_the_runner_does_not_import_the_legacy_calibrator():
    """结构性防线：入口一旦又把旧校准器 import 回来，这条就红。"""
    src = (REPO / "src" / "eval" / "floors" / "runner.py").read_text(encoding="utf-8")
    assert "calibrate_logistic_normal" not in src
    assert "covariance_path" not in src
    assert "scheme_a_path" in src


def test_the_legacy_module_still_exists_for_historical_replay():
    """旧协议**保留**，只是拿不到正式身份。删掉它等于让历史结果无法复现。"""
    assert importlib.util.find_spec("eval.floors.path") is not None
